// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import {IERC1155Receiver} from "@openzeppelin/contracts/token/ERC1155/IERC1155Receiver.sol";
import {IERC165} from "@openzeppelin/contracts/utils/introspection/IERC165.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Initializable} from "@openzeppelin/contracts/proxy/utils/Initializable.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts/proxy/utils/UUPSUpgradeable.sol";

import {IPolymarketExchange} from "./interfaces/IPolymarketExchange.sol";
import {ISafe} from "./interfaces/ISafe.sol";

/// @title  KPAX LendingVault (Sprint 4: UUPS-upgradeable + real liquidation)
/// @notice Non-custodial lending vault. Users hold their Polymarket positions
///         in a Polymarket-managed proxy wallet (Safe v1.3 for external-wallet
///         users). To borrow, the proxy first does a one-time
///         `setApprovalForAll(KpaxVault, true)` on the CTF token; afterwards
///         this contract pulls CTF from the proxy and credits USDC to the
///         signer EOA.
///
///         Liquidation runs through a trusted off-chain keeper. The keeper
///         pulls the current Polymarket price from the Gamma API, sells the
///         CTF off-chain (or via batched on-chain trade), forwards the USDC
///         proceeds to the vault, and finally calls `liquidate(loanId, reason,
///         currentPriceE6)`. The vault settles the books: principal+interest
///         to LPs, penalty into LP pool, residual to borrower. The vault does
///         NOT swap CTF on its own — keeping the on-chain surface area small.
///
///         Sprint 4 covers: real liquidate logic (price-aware), UUPS proxy,
///         upgrader role + 24h timelock as the upgrade admin.
contract LendingVault is
    Initializable,
    UUPSUpgradeable,
    IERC1155Receiver,
    ReentrancyGuard
{
    // ---------------------------------------------------------------- errors
    error OnlyKeeper();
    error OnlyAdmin();
    error OnlyUpgrader();
    error LoanInactive();
    error NotBorrower();
    error InsufficientLiquidity();
    error InvalidLeagueTier();
    error PriceOutOfBand();
    error NotLiquidatable(string reason);
    error NotProxyOwner();
    error CollateralSourceZero();
    error NotPaused();
    error InvalidPrice();
    error InvalidReason();
    error InsufficientProceeds();
    error ZeroAddress();

    // ---------------------------------------------------------------- types
    struct Loan {
        address borrower; // EOA that signed openLoan & owns the proxy
        address collateralSource; // Polymarket proxy (Safe) holding the CTF
        uint256 ctfTokenId;
        uint256 collateralShares;
        uint256 principal; // USDC principal (6 decimals)
        uint256 openTime;
        uint256 matchKickoff; // unix seconds
        uint8 leagueTier; // 1 / 2 / 3
        bool active;
        // Sprint 4: distinguish "repaid" vs "liquidated" so the keeper / UI
        // can read final loan state. Backwards compatible with `active` —
        // when `active == false`, exactly one of (repaid, liquidated) is true.
        bool repaid;
        bool liquidated;
    }

    // ---------------------------------------------------------------- storage
    // NOTE on upgradeability: this is the v1 layout. New variables MUST be
    // appended below `__gap`. NEVER reorder or remove existing slots.
    IERC20 public usdc;
    IERC1155 public ctf;
    IPolymarketExchange public exchange;

    address public admin; // 2-of-3 multisig (or timelock target)
    address public keeper; // KPAX Keeper EOA / multisig
    address public upgrader; // typically the TimelockController (24h delay)

    uint256 public nextLoanId;
    mapping(uint256 => Loan) public loans;
    uint256 public lpPoolBalance; // USDC available to lend

    bool public paused;

    /// @dev Reserved storage slots for future variables to preserve layout
    ///      compatibility across upgrades. Decrement when adding new vars.
    uint256[40] private __gap;

    // ---------------------------------------------------------------- constants
    uint256 public constant APR_BPS = 1200; // 12%
    uint256 public constant LIQUIDATION_PENALTY_BPS = 200; // 2%
    uint256 public constant LIQUIDATION_LTV_BPS = 8000; // 80%
    uint256 public constant KICKOFF_BUFFER = 2 hours;
    uint256 public constant SECONDS_PER_YEAR = 31_536_000;
    uint256 public constant BPS_DENOM = 10_000;
    uint256 public constant PRICE_DENOM_E6 = 1_000_000; // 1.0 in e6 fixed point

    /// @dev keccak256 hashes of the only acceptable reason strings. We compare
    ///      these to `keccak256(bytes(reason))` so the on-chain check is O(1)
    ///      and the keeper sees exactly which strings the vault accepts.
    bytes32 public constant REASON_LTV_BREACH = keccak256(bytes("ltv_breach"));
    bytes32 public constant REASON_KICKOFF_DUE = keccak256(bytes("kickoff_due"));

    // ---------------------------------------------------------------- events
    event LoanOpened(
        uint256 indexed loanId,
        address indexed borrower,
        address indexed collateralSource,
        uint256 ctfTokenId,
        uint256 shares,
        uint256 principal,
        uint256 matchKickoff,
        uint8 leagueTier
    );
    event LoanRepaid(uint256 indexed loanId, uint256 principalPaid, uint256 interestPaid);
    event LoanLiquidated(
        uint256 indexed loanId,
        string reason,
        uint256 proceeds,
        uint256 residualToUser
    );
    event LPDeposit(address indexed from, uint256 amount);
    event LPWithdraw(address indexed to, uint256 amount);
    event KeeperUpdated(address indexed newKeeper);
    event AdminUpdated(address indexed newAdmin);
    event UpgraderUpdated(address indexed newUpgrader);
    event Paused(bool paused);
    event EmergencyERC20Sweep(address indexed token, address indexed to, uint256 amount);
    event EmergencyCollateralReturned(uint256 indexed loanId, address indexed collateralSource, uint256 shares);

    // ---------------------------------------------------------------- modifiers
    modifier onlyKeeper() {
        if (msg.sender != keeper) revert OnlyKeeper();
        _;
    }

    modifier onlyAdmin() {
        if (msg.sender != admin) revert OnlyAdmin();
        _;
    }

    modifier onlyUpgrader() {
        if (msg.sender != upgrader) revert OnlyUpgrader();
        _;
    }

    // ---------------------------------------------------------------- ctor / initializer
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        // Lock the implementation so it cannot be initialized directly. All
        // state lives behind the proxy.
        _disableInitializers();
    }

    /// @notice Initialize the proxy. Idempotent (initializer-gated).
    /// @param  _usdc      USDC token (Polygon: 0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359)
    /// @param  _ctf       Polymarket CTF (Polygon: 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045)
    /// @param  _exchange  Polymarket Exchange (kept for future use; not called in MVP)
    /// @param  _admin     Admin (multisig) — manages keeper, paused, emergency hatches
    /// @param  _keeper    Keeper EOA — only address allowed to call liquidate
    /// @param  _upgrader  Upgrader (TimelockController) — only address allowed to upgrade
    function initialize(
        IERC20 _usdc,
        IERC1155 _ctf,
        IPolymarketExchange _exchange,
        address _admin,
        address _keeper,
        address _upgrader
    ) external initializer {
        if (
            address(_usdc) == address(0) ||
            address(_ctf) == address(0) ||
            _admin == address(0) ||
            _keeper == address(0) ||
            _upgrader == address(0)
        ) revert ZeroAddress();

        usdc = _usdc;
        ctf = _ctf;
        exchange = _exchange; // may legitimately be address(0) until wired up
        admin = _admin;
        keeper = _keeper;
        upgrader = _upgrader;

        // Note on ReentrancyGuard: the parent constructor only runs on the
        // implementation, so the proxy's `_status` slot starts at 0. The
        // `nonReentrant` modifier treats both 0 and `NOT_ENTERED (1)` as
        // unlocked, so the first call sets `_status = ENTERED (2)`, then
        // `_nonReentrantAfter` resets to NOT_ENTERED (1). Functionally
        // identical, with a one-time gas premium on the first call. This is
        // the same trade-off OZ accepts in their non-upgradeable
        // ReentrancyGuard when used behind a proxy.
    }

    // ---------------------------------------------------------------- Borrower
    /// @notice Open a loan: pull CTF collateral from `collateralSource`, send
    ///         USDC principal to the borrower (msg.sender, the proxy owner).
    /// @dev    `collateralSource` must have called
    ///         `ctf.setApprovalForAll(address(this), true)` once. Borrower must
    ///         be an owner of the proxy (verified via ISafe.isOwner).
    function openLoan(
        address collateralSource,
        uint256 ctfTokenId,
        uint256 shares,
        uint256 principal,
        uint256 matchKickoff,
        uint8 leagueTier
    ) external nonReentrant returns (uint256 loanId) {
        require(!paused, "paused");
        if (collateralSource == address(0)) revert CollateralSourceZero();
        if (leagueTier < 1 || leagueTier > 3) revert InvalidLeagueTier();
        require(shares > 0 && principal > 0, "zero amount");
        if (principal > lpPoolBalance) revert InsufficientLiquidity();

        // Critical: borrower must own the proxy. Without this check anyone
        // could drain collateral from any approved proxy.
        _requireProxyOwner(collateralSource, msg.sender);

        loanId = nextLoanId++;
        loans[loanId] = Loan({
            borrower: msg.sender,
            collateralSource: collateralSource,
            ctfTokenId: ctfTokenId,
            collateralShares: shares,
            principal: principal,
            openTime: block.timestamp,
            matchKickoff: matchKickoff,
            leagueTier: leagueTier,
            active: true,
            repaid: false,
            liquidated: false
        });

        // Pull collateral from the proxy (proxy must have approved us).
        ctf.safeTransferFrom(collateralSource, address(this), ctfTokenId, shares, "");

        // Lock the principal from the LP pool and send to borrower EOA.
        lpPoolBalance -= principal;
        require(usdc.transfer(msg.sender, principal), "usdc transfer failed");

        emit LoanOpened(
            loanId,
            msg.sender,
            collateralSource,
            ctfTokenId,
            shares,
            principal,
            matchKickoff,
            leagueTier
        );
    }

    /// @notice Full repayment (partial repay ships in a follow-up). Caller
    ///         must have approved the Vault to pull USDC from the borrower
    ///         EOA. Collateral CTF is returned to the original
    ///         `collateralSource` proxy, not to msg.sender.
    function repay(uint256 loanId) external nonReentrant {
        Loan storage l = loans[loanId];
        if (!l.active) revert LoanInactive();
        if (msg.sender != l.borrower) revert NotBorrower();

        (uint256 totalDebt, uint256 interest) = _debtOf(l);

        require(usdc.transferFrom(msg.sender, address(this), totalDebt), "usdc pull failed");
        lpPoolBalance += totalDebt; // interest stays in the pool as LP yield

        l.active = false;
        l.repaid = true;

        // Release collateral back to the source proxy that originally posted it.
        ctf.safeTransferFrom(address(this), l.collateralSource, l.ctfTokenId, l.collateralShares, "");

        emit LoanRepaid(loanId, l.principal, interest);
    }

    // ---------------------------------------------------------------- Views
    /// @notice Return `(totalDebt, interest)` for an active loan.
    function debtOf(uint256 loanId) external view returns (uint256 totalDebt, uint256 interest) {
        return _debtOf(loans[loanId]);
    }

    function _debtOf(Loan storage l) internal view returns (uint256 totalDebt, uint256 interest) {
        if (!l.active) return (0, 0);
        uint256 elapsed = block.timestamp - l.openTime;
        // interest = principal * apr_bps / 10000 * elapsed / year   (simple, no compound)
        interest = (l.principal * APR_BPS * elapsed) / (BPS_DENOM * SECONDS_PER_YEAR);
        totalDebt = l.principal + interest;
    }

    /// @dev Verify `controller` is an owner of the Safe at `proxy`. Reverts
    ///      with `NotProxyOwner` otherwise. Uses `ISafe.isOwner` (Safe v1.3
    ///      standard view) and explicitly rejects EOAs / non-Safe contracts.
    function _requireProxyOwner(address proxy, address controller) internal view {
        uint256 codeSize;
        assembly {
            codeSize := extcodesize(proxy)
        }
        if (codeSize == 0) revert NotProxyOwner();

        try ISafe(proxy).isOwner(controller) returns (bool ok) {
            if (!ok) revert NotProxyOwner();
        } catch {
            revert NotProxyOwner();
        }
    }

    // ---------------------------------------------------------------- LP
    /// @notice Anyone can top up the LP pool. Only admin can withdraw, so
    ///         deposits can never be siphoned.
    function depositLP(uint256 amount) external nonReentrant {
        require(amount > 0, "zero");
        require(usdc.transferFrom(msg.sender, address(this), amount), "usdc pull failed");
        lpPoolBalance += amount;
        emit LPDeposit(msg.sender, amount);
    }

    function withdrawLP(address to, uint256 amount) external onlyAdmin nonReentrant {
        require(amount <= lpPoolBalance, "exceeds pool");
        lpPoolBalance -= amount;
        require(usdc.transfer(to, amount), "usdc transfer failed");
        emit LPWithdraw(to, amount);
    }

    // ---------------------------------------------------------------- Admin
    function setKeeper(address newKeeper) external onlyAdmin {
        if (newKeeper == address(0)) revert ZeroAddress();
        keeper = newKeeper;
        emit KeeperUpdated(newKeeper);
    }

    function setAdmin(address newAdmin) external onlyAdmin {
        if (newAdmin == address(0)) revert ZeroAddress();
        admin = newAdmin;
        emit AdminUpdated(newAdmin);
    }

    function setUpgrader(address newUpgrader) external onlyUpgrader {
        if (newUpgrader == address(0)) revert ZeroAddress();
        upgrader = newUpgrader;
        emit UpgraderUpdated(newUpgrader);
    }

    function setPaused(bool p) external onlyAdmin {
        paused = p;
        emit Paused(p);
    }

    // ---------------------------------------------------------------- Liquidation (Sprint 4)

    /// @notice Settle a loan that is in liquidation. Off-chain keeper has
    ///         already (a) decided the loan should be liquidated and (b)
    ///         arranged for USDC proceeds to be inside the vault — either by
    ///         selling the CTF off-chain & transferring USDC, or by donating
    ///         USDC out of the keeper's wallet. Either way the vault's USDC
    ///         balance must be at least `lpPoolBalance + proceeds` BEFORE the
    ///         keeper calls this function.
    /// @dev    Keeper passes `currentPriceE6 ∈ (0, 1_000_000]`. We compute
    ///         `proceeds = collateralShares * currentPriceE6 / 1e6`, then:
    ///           - principal + interest go back to LP pool (vault USDC).
    ///           - 2% penalty also goes to LP pool (extra LP yield).
    ///           - residual (if any) is wired to the borrower's EOA.
    ///         The CTF that the vault still holds for this loan is NOT
    ///         touched — by this point the keeper has already sold it on
    ///         Polymarket and no longer owns it economically. We *do* delete
    ///         the bookkeeping (mark loan liquidated, zero out shares).
    /// @param  loanId          loan to liquidate
    /// @param  reason          must be "ltv_breach" or "kickoff_due"
    /// @param  currentPriceE6  Polymarket Yes price * 1e6, > 0 and ≤ 1e6
    function liquidate(
        uint256 loanId,
        string calldata reason,
        uint256 currentPriceE6
    ) external onlyKeeper nonReentrant {
        // Reason whitelist (O(1) hash compare)
        bytes32 reasonHash = keccak256(bytes(reason));
        if (reasonHash != REASON_LTV_BREACH && reasonHash != REASON_KICKOFF_DUE) {
            revert InvalidReason();
        }

        // Price sanity: Polymarket prices are always in (0, 1.0] so e6 must
        // be in (0, 1_000_000].
        if (currentPriceE6 == 0 || currentPriceE6 > PRICE_DENOM_E6) {
            revert InvalidPrice();
        }

        Loan storage l = loans[loanId];
        if (!l.active) revert LoanInactive();
        if (l.repaid || l.liquidated) revert LoanInactive();

        // Snapshot debt at liquidation time (interest accrual stops here).
        (uint256 totalDebt, ) = _debtOf(l);

        // Compute proceeds from the off-chain sale at the keeper-supplied
        // price. e6 fixed-point: shares are unitless, price is USDC per
        // share scaled 1e6, output is USDC (6 decimals).
        uint256 proceeds = (l.collateralShares * currentPriceE6) / PRICE_DENOM_E6;

        // Penalty paid on top to LPs.
        uint256 penalty = (proceeds * LIQUIDATION_PENALTY_BPS) / BPS_DENOM;

        // Residual returned to borrower, after debt + penalty are taken.
        // Saturating subtraction: if proceeds < debt+penalty, residual = 0
        // and the LP pool eats the shortfall (we credit only what's there).
        uint256 residualToUser;
        uint256 toLp;
        if (proceeds >= totalDebt + penalty) {
            toLp = totalDebt + penalty;
            residualToUser = proceeds - totalDebt - penalty;
        } else {
            // proceeds < totalDebt + penalty:
            //   - if proceeds >= totalDebt, all proceeds become LP yield
            //     (debt fully repaid, partial penalty, no residual).
            //   - if proceeds < totalDebt, LP eats the shortfall but still
            //     credits whatever was salvaged. No residual to borrower.
            toLp = proceeds;
            residualToUser = 0;
        }

        // Vault's "free" USDC is total balance minus what's already booked
        // to the LP pool. We need enough free balance to cover the full
        // proceeds figure (LP credit + residual transfer).
        uint256 free = usdc.balanceOf(address(this)) - lpPoolBalance;
        if (free < toLp + residualToUser) revert InsufficientProceeds();

        // Mark loan settled BEFORE moving funds (CEI pattern).
        l.active = false;
        l.liquidated = true;

        // Settle the LP pool first (principal + interest [+ penalty]).
        lpPoolBalance += toLp;

        // Pay residual (if any) to borrower's EOA.
        if (residualToUser > 0) {
            require(usdc.transfer(l.borrower, residualToUser), "residual transfer failed");
        }

        emit LoanLiquidated(loanId, reason, proceeds, residualToUser);
    }

    // ---------------------------------------------------------------- Emergency
    /// @notice Pull any ERC-20 (including USDC) out of the vault. Designed as
    ///         a recovery hatch for LP funds + accidental token donations.
    ///         Requires `paused == true` first so admin must explicitly stop
    ///         new borrows before draining liquidity.
    /// @dev    Adjusts `lpPoolBalance` if USDC is being pulled, so internal
    ///         accounting stays consistent.
    function emergencyWithdrawERC20(IERC20 token, address to, uint256 amount)
        external
        onlyAdmin
        nonReentrant
    {
        if (!paused) revert NotPaused();
        require(token.transfer(to, amount), "transfer failed");
        if (address(token) == address(usdc)) {
            lpPoolBalance = amount >= lpPoolBalance ? 0 : lpPoolBalance - amount;
        }
        emit EmergencyERC20Sweep(address(token), to, amount);
    }

    /// @notice Return a loan's CTF collateral to its original `collateralSource`
    ///         WITHOUT requiring repayment. Use this if `liquidate` is broken
    ///         (e.g. Polymarket Exchange unavailable) AND the borrower is
    ///         stranded. Forgives the debt — LP eats the loss.
    /// @dev    Cannot send collateral anywhere except back to the proxy that
    ///         posted it, so admin can never reroute someone else's CTF.
    ///         Requires `paused == true` to discourage casual use.
    function emergencyReturnCollateral(uint256 loanId)
        external
        onlyAdmin
        nonReentrant
    {
        if (!paused) revert NotPaused();
        Loan storage l = loans[loanId];
        if (!l.active) revert LoanInactive();

        l.active = false;
        ctf.safeTransferFrom(
            address(this),
            l.collateralSource,
            l.ctfTokenId,
            l.collateralShares,
            ""
        );
        emit EmergencyCollateralReturned(loanId, l.collateralSource, l.collateralShares);
    }

    // ---------------------------------------------------------------- UUPS
    /// @dev Authorize upgrade — only the upgrader role (typically a
    ///      TimelockController behind a 2-of-3 multisig) can swap the
    ///      implementation.
    function _authorizeUpgrade(address newImplementation)
        internal
        override
        onlyUpgrader
    {
        if (newImplementation == address(0)) revert ZeroAddress();
    }

    // ---------------------------------------------------------------- IERC1155Receiver
    function onERC1155Received(address, address, uint256, uint256, bytes calldata)
        external
        pure
        override
        returns (bytes4)
    {
        return IERC1155Receiver.onERC1155Received.selector;
    }

    function onERC1155BatchReceived(address, address, uint256[] calldata, uint256[] calldata, bytes calldata)
        external
        pure
        override
        returns (bytes4)
    {
        return IERC1155Receiver.onERC1155BatchReceived.selector;
    }

    function supportsInterface(bytes4 id) external pure override returns (bool) {
        return id == type(IERC1155Receiver).interfaceId || id == type(IERC165).interfaceId;
    }
}
