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

/// @title  KPAX LendingVault V2 — realistic 4-step liquidation
/// @notice Sprint 4 V2 implementation. The V1 stub assumed USDC was already
///         pre-funded into the vault before `liquidate()`. V2 replaces that
///         single atomic call with a four-step keeper-driven flow:
///
///           1. `withdrawCtfForLiquidation(loanId, priceE6)`
///                — vault transfers CTF out to the keeper EOA, marks the
///                  loan as `withdrawn`. Keeper now owns the collateral.
///           2. (off-chain) keeper sells the CTF on Polymarket CLOB for USDC.
///           3. (off-chain) keeper transfers the USDC proceeds back into the
///                vault: `usdc.transfer(vault, actualProceeds)`.
///           4. `settleLiquidation(loanId, reason, actualProceeds)`
///                — vault books the loan as liquidated and distributes the
///                  proceeds: principal → LP pool, interest+penalty → treasury,
///                  residual → borrower.
///
///         If step 2 fails (low liquidity / price collapse), the keeper calls
///         `returnCtfFromKeeper(loanId)` to push the CTF back into the vault
///         and reset `withdrawn = false`. The loan returns to its pre-step-1
///         state and the keeper can retry on the next tick.
///
///         Penalty and interest flow to a project treasury (K13). The treasury
///         starts at `address(0)` post-upgrade; admin must call `setTreasury`
///         before the first liquidation can settle.
///
/// @dev    Storage compatibility notes (audit before deploying):
///
///         V1 layout (top-level):
///           slot 0       _status         (ReentrancyGuard, occupies slot 0)
///           slot 1       usdc
///           slot 2       ctf
///           slot 3       exchange
///           slot 4       admin
///           slot 5       keeper
///           slot 6       upgrader
///           slot 7       nextLoanId
///           slot 8       loans (mapping head)
///           slot 9       lpPoolBalance
///           slot 10      paused
///           slot 11..50  __gap[40]
///
///         V2 takes 1 slot from `__gap` for `treasury`. New layout:
///           slot 11      treasury        (NEW — occupies first reserved slot)
///           slot 12..50  __gap[39]       (one less than V1)
///
///         The `Loan` struct also gains a `bool withdrawn` field. Mapping
///         values are independently keyed by `keccak256(loanId, slot)`, so
///         appending a field does not move surrounding state. Pre-existing
///         loan entries read back `withdrawn == false` because Solidity
///         zero-fills storage that was never written, which is exactly what
///         we want (legacy loans are not "withdrawn").
contract LendingVaultV2 is
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
    // V2-specific errors
    error LoanAlreadyWithdrawn();
    error LoanNotWithdrawn();
    error LoanAlreadyLiquidated();
    error LoanAlreadyRepaid();
    error TreasuryNotSet();

    // ---------------------------------------------------------------- types
    /// @dev V2 adds `withdrawn` at the END of the struct so the V1 storage
    ///      slots inside each mapping entry are byte-for-byte preserved.
    ///      Reading a legacy V1 loan back through this struct returns
    ///      `withdrawn = false` (zero-fill semantics).
    struct Loan {
        address borrower;
        address collateralSource;
        uint256 ctfTokenId;
        uint256 collateralShares;
        uint256 principal;
        uint256 openTime;
        uint256 matchKickoff;
        uint8 leagueTier;
        bool active;
        bool repaid;
        bool liquidated;
        // -------- NEW in V2 --------
        bool withdrawn; // step-1 flag: CTF currently held by keeper, awaiting settle/return
    }

    // ---------------------------------------------------------------- storage
    // Ordering MUST match V1 exactly through `paused`. The ONLY change vs V1
    // is that `treasury` consumes one slot from the original `__gap[40]`.
    IERC20 public usdc;
    IERC1155 public ctf;
    IPolymarketExchange public exchange;

    address public admin;
    address public keeper;
    address public upgrader;

    uint256 public nextLoanId;
    mapping(uint256 => Loan) public loans;
    uint256 public lpPoolBalance;

    bool public paused;

    /// @notice Project-side sink for liquidation interest + penalty. K13 / K14:
    ///         set by admin via `setTreasury` after upgrade. Starts at zero
    ///         intentionally — `settleLiquidation` reverts with
    ///         `TreasuryNotSet()` until admin configures it.
    address public treasury;

    /// @dev Reserved storage. V1 had `__gap[40]`; V2 consumes 1 slot for
    ///      `treasury`, leaving 39.
    uint256[39] private __gap;

    // ---------------------------------------------------------------- constants
    uint256 public constant APR_BPS = 1200; // 12%
    uint256 public constant LIQUIDATION_PENALTY_BPS = 200; // 2%
    uint256 public constant LIQUIDATION_LTV_BPS = 8000; // 80%
    uint256 public constant KICKOFF_BUFFER = 2 hours;
    uint256 public constant SECONDS_PER_YEAR = 31_536_000;
    uint256 public constant BPS_DENOM = 10_000;
    uint256 public constant PRICE_DENOM_E6 = 1_000_000;

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

    /// @notice V2 event — superset of V1's `LoanLiquidated` with explicit
    ///         distribution breakdown so the off-chain decoder doesn't need
    ///         to re-derive the math.
    event LoanLiquidated(
        uint256 indexed loanId,
        string reason,
        uint256 actualProceeds,
        uint256 toLp,
        uint256 toTreasury,
        uint256 residualToBorrower
    );

    /// @notice Step 1 — CTF has left the vault and is now in the keeper's wallet.
    event LiquidationStarted(
        uint256 indexed loanId,
        uint256 currentPriceE6,
        uint256 expectedProceeds
    );

    /// @notice Step-1 reversal — keeper returned the CTF to the vault.
    event CtfReturnedFromKeeper(uint256 indexed loanId);

    event LPDeposit(address indexed from, uint256 amount);
    event LPWithdraw(address indexed to, uint256 amount);
    event KeeperUpdated(address indexed newKeeper);
    event AdminUpdated(address indexed newAdmin);
    event UpgraderUpdated(address indexed newUpgrader);
    event TreasurySet(address indexed previous, address indexed next);
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
        _disableInitializers();
    }

    /// @notice Initial deployment of a fresh V2 proxy. NOT called during a V1→V2
    ///         upgrade — the existing proxy preserves all its V1 state and the
    ///         `treasury` slot starts at zero, which is the documented behavior
    ///         (admin must call `setTreasury` before any liquidation can settle).
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
        exchange = _exchange;
        admin = _admin;
        keeper = _keeper;
        upgrader = _upgrader;
        // treasury intentionally left at address(0); admin sets it explicitly
        // via setTreasury after deployment / upgrade.
    }

    // ---------------------------------------------------------------- Borrower
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
            liquidated: false,
            withdrawn: false
        });

        ctf.safeTransferFrom(collateralSource, address(this), ctfTokenId, shares, "");

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

    /// @notice Repay an active loan in full. CTF goes back to the original
    ///         `collateralSource`. Reverts if the loan is currently in
    ///         step-1-withdrawn state (vault no longer holds the CTF).
    function repay(uint256 loanId) external nonReentrant {
        Loan storage l = loans[loanId];
        if (!l.active) revert LoanInactive();
        if (msg.sender != l.borrower) revert NotBorrower();
        // If keeper has already pulled CTF for liquidation, the borrower can't
        // repay until either the keeper returns the CTF (returnCtfFromKeeper)
        // or the liquidation settles. Otherwise we would have to send CTF the
        // vault no longer holds.
        if (l.withdrawn) revert LoanAlreadyWithdrawn();

        (uint256 totalDebt, uint256 interest) = _debtOf(l);

        require(usdc.transferFrom(msg.sender, address(this), totalDebt), "usdc pull failed");
        lpPoolBalance += totalDebt;

        l.active = false;
        l.repaid = true;

        ctf.safeTransferFrom(address(this), l.collateralSource, l.ctfTokenId, l.collateralShares, "");

        emit LoanRepaid(loanId, l.principal, interest);
    }

    // ---------------------------------------------------------------- Views
    function debtOf(uint256 loanId) external view returns (uint256 totalDebt, uint256 interest) {
        return _debtOf(loans[loanId]);
    }

    function _debtOf(Loan storage l) internal view returns (uint256 totalDebt, uint256 interest) {
        if (!l.active) return (0, 0);
        uint256 elapsed = block.timestamp - l.openTime;
        interest = (l.principal * APR_BPS * elapsed) / (BPS_DENOM * SECONDS_PER_YEAR);
        totalDebt = l.principal + interest;
    }

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

    /// @notice Set the treasury address that receives liquidation interest +
    ///         penalty. Admin-only; can be updated later. Zero address is
    ///         rejected here so admin can never accidentally clear it (and
    ///         freeze settlements). To actually disable settlements, set
    ///         keeper instead.
    function setTreasury(address newTreasury) external onlyAdmin {
        if (newTreasury == address(0)) revert ZeroAddress();
        address previous = treasury;
        treasury = newTreasury;
        emit TreasurySet(previous, newTreasury);
    }

    // ---------------------------------------------------------------- Liquidation V2 (4 steps)

    /// @notice Step 1. Vault releases CTF to the keeper EOA. Marks the loan
    ///         as `withdrawn` to prevent borrower repay / double-withdraw.
    /// @param  loanId          loan to liquidate
    /// @param  currentPriceE6  Polymarket Yes price * 1e6, ∈ (0, 1e6]
    /// @return expectedProceeds shares × priceE6 / 1e6 — keeper-side estimate.
    ///         Real distribution happens in step 4 against `actualProceeds`.
    function withdrawCtfForLiquidation(
        uint256 loanId,
        uint256 currentPriceE6
    ) external onlyKeeper nonReentrant returns (uint256 expectedProceeds) {
        if (currentPriceE6 == 0 || currentPriceE6 > PRICE_DENOM_E6) revert InvalidPrice();

        Loan storage l = loans[loanId];
        if (!l.active) revert LoanInactive();
        if (l.withdrawn) revert LoanAlreadyWithdrawn();
        if (l.liquidated) revert LoanAlreadyLiquidated();
        if (l.repaid) revert LoanAlreadyRepaid();

        // CEI: flip flag before the external CTF transfer.
        l.withdrawn = true;

        expectedProceeds = (l.collateralShares * currentPriceE6) / PRICE_DENOM_E6;

        // Vault → keeper EOA. Keeper now owns the CTF and is responsible for
        // either selling it (settle path) or returning it (revert path).
        ctf.safeTransferFrom(
            address(this),
            msg.sender,
            l.ctfTokenId,
            l.collateralShares,
            ""
        );

        emit LiquidationStarted(loanId, currentPriceE6, expectedProceeds);
    }

    /// @notice Step 4. Books the liquidation and distributes USDC. Caller MUST
    ///         have already deposited `actualProceeds` USDC into the vault
    ///         (`usdc.transfer(vault, actualProceeds)`); this function only
    ///         _verifies_ the free balance is sufficient.
    ///
    ///         Distribution (saturating, exact uint math):
    ///           debt       = principal + interest
    ///           penalty    = actualProceeds × 200 / 10_000
    ///           toLp       = min(actualProceeds, principal)
    ///           remaining  = actualProceeds - toLp
    ///           toTreasury = min(remaining, interest + penalty)
    ///           residual   = remaining - toTreasury  (always ≥ 0 by saturating)
    ///
    ///         Notes:
    ///         - Interest is recomputed at settle time from the original
    ///           openTime; clock continues to tick between step 1 and step 4
    ///           which is what we want (keeper drag is a real cost).
    ///         - `toLp` caps at `principal` only — under-recovery means LP
    ///           gets back less than principal and treasury/borrower get 0.
    ///         - If `actualProceeds` exceeds principal but not principal +
    ///           interest + penalty, the treasury share is capped at
    ///           `actualProceeds - principal` and residual is 0.
    /// @param  loanId          loan to settle
    /// @param  reason          must be "ltv_breach" or "kickoff_due"
    /// @param  actualProceeds  USDC the keeper actually realised on the sale
    function settleLiquidation(
        uint256 loanId,
        string calldata reason,
        uint256 actualProceeds
    ) external onlyKeeper nonReentrant {
        bytes32 reasonHash = keccak256(bytes(reason));
        if (reasonHash != REASON_LTV_BREACH && reasonHash != REASON_KICKOFF_DUE) {
            revert InvalidReason();
        }

        Loan storage l = loans[loanId];
        if (!l.withdrawn) revert LoanNotWithdrawn();
        if (l.liquidated) revert LoanAlreadyLiquidated();
        if (l.repaid) revert LoanAlreadyRepaid();

        // Treasury must be configured before any settlement can happen — this
        // is intentional: post-upgrade the slot is zero, admin must explicitly
        // call setTreasury before the first liquidation can be processed.
        address _treasury = treasury;
        if (_treasury == address(0)) revert TreasuryNotSet();

        // Interest at settle time. Once we mark liquidated below the function
        // returns 0 from _debtOf, so cache it now.
        (, uint256 interest) = _debtOf(l);
        uint256 principal = l.principal;
        address borrower = l.borrower;

        // Free balance check — vault must hold AT LEAST `lpPoolBalance + actualProceeds`
        // worth of USDC, i.e. enough to fund LP credit + treasury + residual.
        uint256 free = usdc.balanceOf(address(this)) - lpPoolBalance;
        if (free < actualProceeds) revert InsufficientProceeds();

        uint256 penalty = (actualProceeds * LIQUIDATION_PENALTY_BPS) / BPS_DENOM;

        // Saturating waterfall: LP first, then treasury, then borrower.
        uint256 toLp = actualProceeds < principal ? actualProceeds : principal;
        uint256 remaining = actualProceeds - toLp;

        uint256 treasuryDue = interest + penalty;
        uint256 toTreasury = remaining < treasuryDue ? remaining : treasuryDue;
        uint256 residualToBorrower = remaining - toTreasury;

        // CEI: flip state BEFORE moving funds.
        l.active = false;
        l.liquidated = true;
        // intentionally leave l.withdrawn = true so the bookkeeping reflects
        // "CTF was sold by keeper, never returning to vault"; keeper holds 0
        // CTF for this loan post-sale anyway.

        // Apply distribution.
        if (toLp > 0) {
            lpPoolBalance += toLp;
        }
        if (toTreasury > 0) {
            require(usdc.transfer(_treasury, toTreasury), "treasury transfer failed");
        }
        if (residualToBorrower > 0) {
            require(usdc.transfer(borrower, residualToBorrower), "residual transfer failed");
        }

        emit LoanLiquidated(
            loanId,
            reason,
            actualProceeds,
            toLp,
            toTreasury,
            residualToBorrower
        );
    }

    /// @notice Step-1 reversal. Keeper failed to sell the CTF (no liquidity,
    ///         price collapse, manual abort) and pushes it back into the
    ///         vault. Loan returns to `active && !withdrawn`.
    /// @dev    Keeper EOA must have done a one-time
    ///         `ctf.setApprovalForAll(vault, true)` before this works; we
    ///         don't enforce it here because ERC1155 itself reverts if the
    ///         vault can't pull tokens.
    function returnCtfFromKeeper(uint256 loanId) external onlyKeeper nonReentrant {
        Loan storage l = loans[loanId];
        if (!l.withdrawn) revert LoanNotWithdrawn();
        if (l.liquidated) revert LoanAlreadyLiquidated();
        if (l.repaid) revert LoanAlreadyRepaid();

        // CEI: flip flag before pull.
        l.withdrawn = false;

        ctf.safeTransferFrom(
            msg.sender,
            address(this),
            l.ctfTokenId,
            l.collateralShares,
            ""
        );

        emit CtfReturnedFromKeeper(loanId);
    }

    // ---------------------------------------------------------------- Emergency
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

    function emergencyReturnCollateral(uint256 loanId)
        external
        onlyAdmin
        nonReentrant
    {
        if (!paused) revert NotPaused();
        Loan storage l = loans[loanId];
        if (!l.active) revert LoanInactive();
        // V2 guard: vault must currently hold the CTF (not in keeper hands).
        if (l.withdrawn) revert LoanAlreadyWithdrawn();

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
