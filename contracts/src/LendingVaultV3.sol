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

/// @notice Polymarket V2 collateral wrappers.
///   - Onramp.wrap (USDC.e → pUSD): no auth — anyone can call.
///   - Offramp.unwrap (pUSD → USDC.e): no auth — anyone can call. The vault
///     pre-approves the offramp on its pUSD balance during initializeV3.
interface ICollateralOnramp {
    function wrap(address asset, address to, uint256 amount) external;
}

interface ICollateralOfframp {
    function unwrap(address asset, address to, uint256 amount) external;
}

/// @title  KPAX LendingVault V3 — pUSD-routed lending
/// @notice Sprint 5 V3. After PM CLOB V2 cutover (2026-04-28), the only token
///         PM accepts as collateral is **pUSD**. The vault's underlying
///         reserve token switches from Native USDC to **USDC.e** (the only
///         input asset PM Onramp accepts), and:
///
///           - openLoan: USDC.e is wrapped to pUSD and sent to the borrower's
///             PM V2 DepositWallet (`borrowerProxy`), so they can immediately
///             trade on PM CLOB V2.
///           - repay: pUSD is pulled from the borrower's V2 DepositWallet
///             (msg.sender = proxy), unwrapped to USDC.e, credited to LP.
///           - settleLiquidation: keeper has pre-transferred pUSD to the vault
///             (step 4 of the 5-step flow); vault unwraps pUSD → USDC.e and
///             distributes per the V2 waterfall (LP / treasury / borrower).
///
///         LP's experience is unchanged: depositLP / withdrawLP still take
///         and return the vault's underlying token. Only the underlying
///         identity changed (Native USDC → USDC.e).
///
/// @dev    Storage compatibility (audit before deploying):
///
///         V2 layout (top-level), preserved exactly here:
///           slot 0        _status               (ReentrancyGuard)
///           slot 1        usdc                  ← V3 init OVERWRITES w/ USDC.e
///           slot 2        ctf
///           slot 3        exchange
///           slot 4        admin
///           slot 5        keeper
///           slot 6        upgrader
///           slot 7        nextLoanId
///           slot 8        loans (mapping head)
///           slot 9        lpPoolBalance
///           slot 10:0     paused (bool, 1 byte)
///           slot 10:1     treasury (address packs with `paused` in slot 10)
///           slot 11..49   __gap[39]             (preserved)
///
///         V3-only fields (pUSD/onramp/offramp/borrowerProxy) live in an
///         ERC-7201 namespaced slot (`kpax.lending.vault.v3`) so they cannot
///         collide with future V2/V3 storage additions.
///
///         The Loan struct is also preserved exactly, including the V2
///         `withdrawn` field. New per-loan state (the borrower's V2 DepositWallet
///         used as repay caller) lives in V3 ERC-7201 storage as a separate
///         mapping(uint256 => address), not on the Loan struct, to avoid any
///         struct-layout risk.
contract LendingVaultV3 is
    Initializable,
    UUPSUpgradeable,
    IERC1155Receiver,
    ReentrancyGuard
{
    // ---------------------------------------------------------------- errors
    // V1/V2 errors preserved by selector for off-chain decoders.
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
    error LoanAlreadyWithdrawn();
    error LoanNotWithdrawn();
    error LoanAlreadyLiquidated();
    error LoanAlreadyRepaid();
    error TreasuryNotSet();
    // V3-specific
    error V3NotInitialized();
    error BorrowerProxyZero();
    error WrongRepayCaller();

    // ---------------------------------------------------------------- types
    /// @dev Layout MUST match V2's Loan exactly (byte-for-byte) so existing
    ///      `loans[id]` storage entries still decode correctly. Order, types,
    ///      and packing all preserved.
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
        bool withdrawn;
    }

    // ---------------------------------------------------------------- V2 storage (preserved exactly)
    IERC20 public usdc;                  // slot 1 — V3 init swaps to USDC.e
    IERC1155 public ctf;                 // slot 2
    IPolymarketExchange public exchange; // slot 3

    address public admin;    // slot 4
    address public keeper;   // slot 5
    address public upgrader; // slot 6

    uint256 public nextLoanId;             // slot 7
    mapping(uint256 => Loan) public loans; // slot 8
    uint256 public lpPoolBalance;          // slot 9

    bool public paused;       // slot 10
    address public treasury;  // slot 11

    /// @dev V2's __gap was [39] after carving out treasury. Preserved.
    uint256[39] private __gap;

    // ---------------------------------------------------------------- V3 storage (ERC-7201)
    /// @custom:storage-location erc7201:kpax.lending.vault.v3
    struct V3Storage {
        address pUSD;
        address onramp;
        address offramp;
        // loanId → borrower's PM V2 DepositWallet. msg.sender for V3 repay.
        mapping(uint256 => address) borrowerProxy;
    }

    // keccak256(abi.encode(uint256(keccak256("kpax.lending.vault.v3")) - 1)) & ~bytes32(uint256(0xff))
    bytes32 private constant V3_STORAGE_LOCATION =
        0x633b8825432c4ddb4404efd0d33dcded7cc91caf332a02dde09366c5de3e4c00;

    function _v3() private pure returns (V3Storage storage $) {
        bytes32 slot = V3_STORAGE_LOCATION;
        assembly {
            $.slot := slot
        }
    }

    // ---------------------------------------------------------------- constants
    uint256 public constant APR_BPS = 1200;             // 12%
    uint256 public constant LIQUIDATION_PENALTY_BPS = 200; // 2%
    uint256 public constant LIQUIDATION_LTV_BPS = 8000; // 80%
    uint256 public constant KICKOFF_BUFFER = 2 hours;
    uint256 public constant SECONDS_PER_YEAR = 31_536_000;
    uint256 public constant BPS_DENOM = 10_000;
    uint256 public constant PRICE_DENOM_E6 = 1_000_000;

    bytes32 public constant REASON_LTV_BREACH = keccak256(bytes("ltv_breach"));
    bytes32 public constant REASON_KICKOFF_DUE = keccak256(bytes("kickoff_due"));

    // ---------------------------------------------------------------- events
    // V1/V2 events preserved for indexer compatibility.
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
        uint256 actualProceeds,
        uint256 toLp,
        uint256 toTreasury,
        uint256 residualToBorrower
    );
    event LiquidationStarted(
        uint256 indexed loanId,
        uint256 currentPriceE6,
        uint256 expectedProceeds
    );
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

    // V3-only.
    event V3Initialized(address indexed usdce, address indexed pUSD, address onramp, address offramp);
    event LoanOpenedV3(uint256 indexed loanId, address indexed borrowerProxy, uint256 principalPusd);
    event EmergencyERC1155Sweep(
        address indexed token,
        address indexed to,
        uint256 indexed tokenId,
        uint256 amount
    );

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

    /// @notice Fresh-deploy initializer. NOT used during a V2→V3 proxy upgrade
    ///         (existing proxies preserve all V1/V2 state and only call
    ///         `initializeV3`). Used by tests + any greenfield proxy.
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
    }

    /// @notice One-shot V2→V3 hook. Run via `upgradeToAndCall(impl, initData)`
    ///         where initData = abi.encodeCall(initializeV3, ...). Must run
    ///         exactly once after the upgrade.
    ///
    ///         Effects:
    ///           1. Switches `usdc` slot from Native USDC to USDC.e (PM Onramp
    ///              only accepts USDC.e — verified in Phase 1 §3.1).
    ///           2. Wires pUSD / Onramp / Offramp into V3 ERC-7201 storage.
    ///           3. Max-approves Onramp to pull USDC.e and Offramp to pull
    ///              pUSD from this vault.
    ///
    ///         Pre-conditions (admin responsibility, NOT enforced here):
    ///           - Vault's existing Native USDC balance has been swept via
    ///             emergencyWithdrawERC20 (paused state).
    ///           - All active V2 loans have been settled / returned via
    ///             emergencyReturnCollateral. lpPoolBalance is 0.
    ///           - Treasury is already set on V2; setting persists across upgrade.
    ///
    ///         `reinitializer(2)` ensures this function can only be invoked
    ///         once on a given proxy AND only at version >= 2 (so an attacker
    ///         can't downgrade-replay it).
    function initializeV3(
        IERC20 _usdce,
        address _pUSD,
        address _onramp,
        address _offramp
    ) external reinitializer(2) {
        if (
            address(_usdce) == address(0) ||
            _pUSD == address(0) ||
            _onramp == address(0) ||
            _offramp == address(0)
        ) revert ZeroAddress();

        // Switch underlying reserve token.
        usdc = _usdce;

        V3Storage storage $ = _v3();
        $.pUSD = _pUSD;
        $.onramp = _onramp;
        $.offramp = _offramp;

        // Max-approve ramps. Approve always returns true on USDC.e / pUSD
        // (both are standard OpenZeppelin-style ERC20s), but we still check
        // to be defensive against future token redeploys.
        require(_usdce.approve(_onramp, type(uint256).max), "usdce approve onramp failed");
        require(IERC20(_pUSD).approve(_offramp, type(uint256).max), "pusd approve offramp failed");

        emit V3Initialized(address(_usdce), _pUSD, _onramp, _offramp);
    }

    // ---------------------------------------------------------------- Borrower

    /// @notice V3 openLoan. USDC.e is wrapped to pUSD via Onramp and lands in
    ///         `borrowerProxy` (the borrower's PM V2 DepositWallet). The
    ///         borrower never sees USDC.e or pUSD on their EOA — pUSD is
    ///         immediately tradeable on PM CLOB V2 from the proxy.
    /// @param  collateralSource  legacy/V2 PM proxy holding the CTF (msg.sender
    ///                           must be an owner)
    /// @param  borrowerProxy     borrower's PM V2 DepositWallet — destination
    ///                           for the wrapped pUSD; also the registered
    ///                           caller for `repay`.
    function openLoan(
        address collateralSource,
        uint256 ctfTokenId,
        uint256 shares,
        uint256 principal,
        uint256 matchKickoff,
        uint8 leagueTier,
        address borrowerProxy
    ) external nonReentrant returns (uint256 loanId) {
        require(!paused, "paused");
        if (collateralSource == address(0)) revert CollateralSourceZero();
        if (borrowerProxy == address(0)) revert BorrowerProxyZero();
        if (leagueTier < 1 || leagueTier > 3) revert InvalidLeagueTier();
        require(shares > 0 && principal > 0, "zero amount");
        if (principal > lpPoolBalance) revert InsufficientLiquidity();

        V3Storage storage $ = _v3();
        if ($.onramp == address(0)) revert V3NotInitialized();

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
        $.borrowerProxy[loanId] = borrowerProxy;

        ctf.safeTransferFrom(collateralSource, address(this), ctfTokenId, shares, "");

        lpPoolBalance -= principal;

        // Wrap USDC.e → pUSD into borrowerProxy. Onramp is unauth'd; we
        // pre-approved it to spend our USDC.e in initializeV3.
        ICollateralOnramp($.onramp).wrap(address(usdc), borrowerProxy, principal);

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
        emit LoanOpenedV3(loanId, borrowerProxy, principal);
    }

    /// @notice V3 repay. Caller MUST be the borrower's V2 DepositWallet
    ///         (`borrowerProxy` registered at openLoan). Pulls pUSD from the
    ///         proxy, unwraps to USDC.e, credits LP, and returns CTF to the
    ///         original `collateralSource`.
    ///
    ///         Trigger flow (off-chain): borrower signs an EIP-712 Batch via
    ///         their own PM Relayer key, doing
    ///           [pUSD.approve(vault, totalDebt), vault.repay(loanId)]
    ///         in one Batch. msg.sender during repay = borrower's V2 proxy.
    function repay(uint256 loanId) external nonReentrant {
        Loan storage l = loans[loanId];
        if (!l.active) revert LoanInactive();
        if (l.withdrawn) revert LoanAlreadyWithdrawn();

        V3Storage storage $ = _v3();
        address proxy = $.borrowerProxy[loanId];
        if (proxy == address(0) || msg.sender != proxy) revert WrongRepayCaller();

        (uint256 totalDebt, uint256 interest) = _debtOf(l);

        // Pull pUSD from the proxy (proxy must have approved the vault first).
        require(
            IERC20($.pUSD).transferFrom(msg.sender, address(this), totalDebt),
            "pusd pull failed"
        );
        // Unwrap pUSD → USDC.e (lands in this vault's USDC.e balance). Offramp
        // was max-approved on our pUSD in initializeV3.
        ICollateralOfframp($.offramp).unwrap(address(usdc), address(this), totalDebt);

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

    function borrowerProxyOf(uint256 loanId) external view returns (address) {
        return _v3().borrowerProxy[loanId];
    }

    function pUSD() external view returns (address) {
        return _v3().pUSD;
    }

    function onramp() external view returns (address) {
        return _v3().onramp;
    }

    function offramp() external view returns (address) {
        return _v3().offramp;
    }

    // ---------------------------------------------------------------- LP (USDC.e in V3)
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

    function setTreasury(address newTreasury) external onlyAdmin {
        if (newTreasury == address(0)) revert ZeroAddress();
        address previous = treasury;
        treasury = newTreasury;
        emit TreasurySet(previous, newTreasury);
    }

    // ---------------------------------------------------------------- Liquidation V3 (5-step keeper flow)

    /// @notice Step 1 — vault releases CTF to keeper EOA. Identical to V2.
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

        l.withdrawn = true;

        expectedProceeds = (l.collateralShares * currentPriceE6) / PRICE_DENOM_E6;

        ctf.safeTransferFrom(
            address(this),
            msg.sender,
            l.ctfTokenId,
            l.collateralShares,
            ""
        );

        emit LiquidationStarted(loanId, currentPriceE6, expectedProceeds);
    }

    /// @notice Step 5 — keeper has already pre-transferred `actualProceeds`
    ///         pUSD to this vault (step 4 of the off-chain flow). This method
    ///         unwraps the pUSD to USDC.e and runs the V2 distribution
    ///         waterfall in USDC.e space.
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

        address _treasury = treasury;
        if (_treasury == address(0)) revert TreasuryNotSet();

        // V3: vault must hold ≥ actualProceeds in pUSD (keeper pre-transferred
        // it via PM Relayer in step 4). Unwrap to USDC.e in one shot.
        V3Storage storage $ = _v3();
        if (IERC20($.pUSD).balanceOf(address(this)) < actualProceeds) revert InsufficientProceeds();
        ICollateralOfframp($.offramp).unwrap(address(usdc), address(this), actualProceeds);

        // Distribution math (USDC.e space) — same waterfall as V2.
        (, uint256 interest) = _debtOf(l);
        uint256 principal = l.principal;
        address borrower = l.borrower;

        // Free balance check, post-unwrap. After the unwrap above, the vault's
        // USDC.e balance grew by actualProceeds, so this should pass unless
        // someone front-ran the keeper transfer (defensive).
        uint256 free = usdc.balanceOf(address(this)) - lpPoolBalance;
        if (free < actualProceeds) revert InsufficientProceeds();

        uint256 penalty = (actualProceeds * LIQUIDATION_PENALTY_BPS) / BPS_DENOM;

        uint256 toLp = actualProceeds < principal ? actualProceeds : principal;
        uint256 remaining = actualProceeds - toLp;

        uint256 treasuryDue = interest + penalty;
        uint256 toTreasury = remaining < treasuryDue ? remaining : treasuryDue;
        uint256 residualToBorrower = remaining - toTreasury;

        l.active = false;
        l.liquidated = true;

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

    /// @notice Step-1 reversal. Identical to V2.
    function returnCtfFromKeeper(uint256 loanId) external onlyKeeper nonReentrant {
        Loan storage l = loans[loanId];
        if (!l.withdrawn) revert LoanNotWithdrawn();
        if (l.liquidated) revert LoanAlreadyLiquidated();
        if (l.repaid) revert LoanAlreadyRepaid();

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

    /// @notice V3-only ERC1155 sweep for paused state. Defensive: lets admin
    ///         recover any CTF accidentally stuck in the vault outside the
    ///         normal loan lifecycle.
    function emergencyWithdrawERC1155(
        IERC1155 token,
        address to,
        uint256 tokenId,
        uint256 amount
    ) external onlyAdmin nonReentrant {
        if (!paused) revert NotPaused();
        token.safeTransferFrom(address(this), to, tokenId, amount, "");
        emit EmergencyERC1155Sweep(address(token), to, tokenId, amount);
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
