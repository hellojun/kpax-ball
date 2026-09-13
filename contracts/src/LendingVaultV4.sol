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

/// PM Offramp interface — V4 still uses it for keeper settle. The keeper
/// sells CTF for pUSD on PM CLOB V2 (proxy-side), pre-transfers pUSD to
/// vault, and `settleLiquidation` unwraps pUSD → USDC.e here. Same as V3.
interface ICollateralOfframp {
    function unwrap(address asset, address to, uint256 amount) external;
}

/// @title  KPAX LendingVault V4 — direct USDC.e borrow / repay
/// @notice Sprint 5.5. V3 attempted to wrap USDC.e → pUSD on borrow + send
///         pUSD into the borrower's PM V2 DepositWallet so they could
///         immediately trade on PM CLOB V2. That broke when Polymarket
///         migrated from EIP-1967 CREATE2 DepositWallets to EIP-7702
///         delegated EOAs (post 2026-04-28 cutover): `derive_v2_deposit_wallet`
///         predicts an address PM no longer deploys, so wrapped pUSD lands
///         at a CREATE2-derived address with no code and no signer — funds
///         lock permanently.
///
///         V4 abandons the "wrap-into-PM-wallet" model and falls back to a
///         simpler design:
///
///           - openLoan: vault transfers USDC.e *directly* to the borrower
///                        EOA. The borrower handles depositing into PM
///                        themselves (any token, any chain — PM's UI accepts
///                        all of them and credits the right balance).
///           - repay:    borrower EOA pulls USDC.e back via approve +
///                        transferFrom. Same flow as the original V1/V2.
///           - settleLiquidation: KEPT from V3. Keeper still sells CTF on
///                        PM CLOB V2 (which only accepts pUSD), pre-transfers
///                        pUSD to vault via PM Relayer, and this function
///                        unwraps pUSD → USDC.e + distributes the V2 waterfall.
///
///         Trade-off: borrower has to do an extra deposit step on Polymarket
///         after receiving USDC.e on their EOA. UX worse than V3's promise of
///         "borrowed pUSD is instantly tradeable on PM", but V3's promise was
///         broken by the EIP-7702 migration anyway. V4 is correct + works.
///
/// @dev    Storage layout INHERITED from V3 (unchanged). V4 doesn't introduce
///         any new top-level slots; it does NOT use V3's ERC-7201 fields
///         (`pUSD`, `onramp`, `borrowerProxy[]`) on the borrow/repay paths,
///         but they remain readable for backwards compatibility AND for V3's
///         settleLiquidation behavior which V4 preserves verbatim.
///
///         No `initializeV4` is needed — V3's `initializeV3` already wired
///         `usdc → USDC.e`, `pUSD`, `onramp`, `offramp`, and the max
///         allowances. V4 reuses all of that.
contract LendingVaultV4 is
    Initializable,
    UUPSUpgradeable,
    IERC1155Receiver,
    ReentrancyGuard
{
    // ---------------------------------------------------------------- errors
    // V1/V2/V3 errors preserved by selector for off-chain decoders.
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
    // V3 errors preserved (V4 keeps the V3 storage / settleLiquidation logic).
    error V3NotInitialized();

    // ---------------------------------------------------------------- types
    /// Layout MUST match V2/V3 exactly. Mapping values are independently keyed,
    /// so `loans[id]` written under V2/V3 still decodes correctly here.
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

    // ---------------------------------------------------------------- V1/V2/V3 storage (preserved exactly)
    IERC20 public usdc;                  // slot 1 — set to USDC.e by V3 init
    IERC1155 public ctf;                 // slot 2
    IPolymarketExchange public exchange; // slot 3

    address public admin;    // slot 4
    address public keeper;   // slot 5
    address public upgrader; // slot 6

    uint256 public nextLoanId;             // slot 7
    mapping(uint256 => Loan) public loans; // slot 8
    uint256 public lpPoolBalance;          // slot 9

    bool public paused;       // slot 10:0
    address public treasury;  // slot 10:1

    /// @dev V2 carved 1 slot out of V1's __gap[40]; V3/V4 keep 39.
    uint256[39] private __gap;

    // ---------------------------------------------------------------- V3 ERC-7201 storage (still readable)
    /// V4 does NOT use `pUSD` / `onramp` / `borrowerProxy[]` on the borrow path,
    /// but `pUSD` and `offramp` are still consumed by `settleLiquidation` (V3
    /// behavior preserved). Layout matches V3.
    /// @custom:storage-location erc7201:kpax.lending.vault.v3
    struct V3Storage {
        address pUSD;
        address onramp;
        address offramp;
        mapping(uint256 => address) borrowerProxy; // legacy V3 — V4 doesn't write here
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
    uint256 public constant APR_BPS = 1200;
    uint256 public constant LIQUIDATION_PENALTY_BPS = 200;
    uint256 public constant LIQUIDATION_LTV_BPS = 8000;
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

    // ---------------------------------------------------------------- ctor
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    /// @notice Fresh-deploy initializer. NOT used during V3 → V4 upgrade.
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

    // ---------------------------------------------------------------- Borrower (V4: USDC.e direct)

    /// @notice V4 openLoan. USDC.e is transferred *directly* to the borrower
    ///         EOA (msg.sender). The borrower deposits into Polymarket
    ///         themselves via PM's UI; KPAX makes no assumption about PM's
    ///         deposit-address derivation (which changed when PM moved to
    ///         EIP-7702 delegated EOAs).
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

    /// @notice V4 repay. Borrower EOA calls directly with USDC.e approval.
    function repay(uint256 loanId) external nonReentrant {
        Loan storage l = loans[loanId];
        if (!l.active) revert LoanInactive();
        if (msg.sender != l.borrower) revert NotBorrower();
        if (l.withdrawn) revert LoanAlreadyWithdrawn();

        (uint256 totalDebt, uint256 interest) = _debtOf(l);

        require(
            usdc.transferFrom(msg.sender, address(this), totalDebt),
            "usdc pull failed"
        );
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

    /// V3 storage views — kept for backwards compatibility with anything that
    /// still reads `pUSD()` / `onramp()` / `offramp()` (e.g. keeper for
    /// settleLiquidation pre-transfer plumbing).
    function pUSD() external view returns (address) {
        return _v3().pUSD;
    }

    function onramp() external view returns (address) {
        return _v3().onramp;
    }

    function offramp() external view returns (address) {
        return _v3().offramp;
    }

    // ---------------------------------------------------------------- LP (USDC.e)
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

    // ---------------------------------------------------------------- Liquidation V4

    /// @notice Step 1 — vault releases CTF to keeper EOA. Identical to V2/V3.
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

    /// @notice Step 5 — settleLiquidation. UNCHANGED from V3: keeper has
    ///         pre-transferred `actualProceeds` pUSD to this vault (PM CLOB V2
    ///         settles in pUSD). Vault unwraps to USDC.e + distributes.
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

        V3Storage storage $ = _v3();
        if (IERC20($.pUSD).balanceOf(address(this)) < actualProceeds) revert InsufficientProceeds();
        ICollateralOfframp($.offramp).unwrap(address(usdc), address(this), actualProceeds);

        (, uint256 interest) = _debtOf(l);
        uint256 principal = l.principal;
        address borrower = l.borrower;

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

    /// @notice Step-1 reversal. Identical to V2/V3.
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
