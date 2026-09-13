// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";

import {LendingVaultV3} from "../src/LendingVaultV3.sol";
import {LendingVaultV4} from "../src/LendingVaultV4.sol";
import {IPolymarketExchange} from "../src/interfaces/IPolymarketExchange.sol";

import {MockUSDC} from "./mocks/MockUSDC.sol";
import {MockCTF} from "./mocks/MockCTF.sol";
import {MockExchange} from "./mocks/MockExchange.sol";
import {MockSafe} from "./mocks/MockSafe.sol";
import {MockOnramp, MockOfframp} from "./mocks/MockCollateralRamp.sol";

/// V4 test suite — direct USDC.e borrow/repay (no PM wallet wrapping).
/// settleLiquidation behavior is preserved from V3 so the keeper flow tests
/// from V3 still apply; we only re-test the V4-specific deltas here.
contract LendingVaultV4Test is Test {
    MockUSDC usdce;
    MockUSDC pusd;
    MockCTF ctf;
    MockExchange ex;
    MockOnramp onramp;
    MockOfframp offramp;

    LendingVaultV4 vault;

    address admin = address(0xA11CE);
    address keeper = address(0xBEEF);
    address upgrader = address(0xDA0DA0);
    address treasury = address(0xCB7A);

    address alice = address(0xA1);
    MockSafe aliceSafe;

    uint256 constant CTF_ID = 42;
    uint256 constant ALICE_SHARES = 1_000_000_000;
    uint256 constant PRINCIPAL = 250e6;
    uint256 constant LP_SEED = 10_000e6;

    function setUp() public {
        usdce = new MockUSDC();
        pusd = new MockUSDC();
        ctf = new MockCTF();
        ex = new MockExchange();
        onramp = new MockOnramp(address(usdce), address(pusd));
        offramp = new MockOfframp(address(usdce), address(pusd));

        // Production-equivalent bootstrap: V3 impl + proxy → V3 init → upgrade
        // to V4 impl. V4 has no `initializeV4` because it doesn't add storage,
        // so wiring of pUSD / onramp / offramp / USDC.e all happens at V3
        // init time and persists across the upgrade.
        LendingVaultV3 v3impl = new LendingVaultV3();
        bytes memory initData = abi.encodeCall(
            LendingVaultV3.initialize,
            (
                IERC20(address(usdce)),
                IERC1155(address(ctf)),
                IPolymarketExchange(address(ex)),
                admin,
                keeper,
                upgrader
            )
        );
        ERC1967Proxy proxy = new ERC1967Proxy(address(v3impl), initData);

        // Wire V3 ERC-7201 storage.
        vm.prank(admin);
        LendingVaultV3(address(proxy)).initializeV3(
            IERC20(address(usdce)), address(pusd), address(onramp), address(offramp)
        );

        // Upgrade impl to V4 in place. No init call — V4 reuses V3 storage.
        LendingVaultV4 v4impl = new LendingVaultV4();
        vm.prank(upgrader);
        LendingVaultV3(address(proxy)).upgradeToAndCall(address(v4impl), "");
        vault = LendingVaultV4(address(proxy));

        vm.prank(admin);
        vault.setTreasury(treasury);

        usdce.mint(admin, LP_SEED);
        vm.startPrank(admin);
        usdce.approve(address(vault), LP_SEED);
        vault.depositLP(LP_SEED);
        vm.stopPrank();

        aliceSafe = new MockSafe(alice);
        ctf.mint(address(aliceSafe), CTF_ID, ALICE_SHARES);
        aliceSafe.setApprovalForAll1155(IERC1155(address(ctf)), address(vault), true);

        vm.prank(keeper);
        ctf.setApprovalForAll(address(vault), true);
    }

    // ============================================================ openLoan (V4: USDC.e direct)

    function _openLoanV4(uint256 principal) internal returns (uint256 loanId) {
        vm.prank(alice);
        loanId = vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            principal,
            block.timestamp + 7 days,
            1
        );
    }

    function test_OpenLoan_UsdceLandsInBorrowerEoa() public {
        uint256 aliceUsdceBefore = usdce.balanceOf(alice);

        uint256 loanId = _openLoanV4(PRINCIPAL);

        // USDC.e went *directly* to alice's EOA (no PM wrap).
        assertEq(
            usdce.balanceOf(alice) - aliceUsdceBefore,
            PRINCIPAL,
            "alice EOA received USDC.e"
        );
        // No pUSD touched.
        assertEq(pusd.balanceOf(alice), 0, "no pUSD anywhere");
        assertEq(pusd.totalSupply(), 0, "no pUSD minted");
        // CTF moved into vault.
        assertEq(ctf.balanceOf(address(vault), CTF_ID), ALICE_SHARES, "CTF in vault");
        // Vault USDC.e drained by principal exactly.
        assertEq(usdce.balanceOf(address(vault)), LP_SEED - PRINCIPAL, "vault drained");
        // lpPoolBalance accounting matches.
        assertEq(vault.lpPoolBalance(), LP_SEED - PRINCIPAL);

        ( , , , , , , , , bool active, , , ) = vault.loans(loanId);
        assertTrue(active);
    }

    function test_OpenLoan_RevertsCollateralSourceZero() public {
        vm.prank(alice);
        vm.expectRevert(LendingVaultV4.CollateralSourceZero.selector);
        vault.openLoan(address(0), CTF_ID, ALICE_SHARES, PRINCIPAL, block.timestamp + 7 days, 1);
    }

    function test_OpenLoan_RevertsNotProxyOwner() public {
        address mallory = address(0xBAD);
        vm.prank(mallory);
        vm.expectRevert(LendingVaultV4.NotProxyOwner.selector);
        vault.openLoan(address(aliceSafe), CTF_ID, ALICE_SHARES, PRINCIPAL, block.timestamp + 7 days, 1);
    }

    function test_OpenLoan_RevertsInsufficientLiquidity() public {
        vm.prank(alice);
        vm.expectRevert(LendingVaultV4.InsufficientLiquidity.selector);
        vault.openLoan(address(aliceSafe), CTF_ID, ALICE_SHARES, LP_SEED + 1, block.timestamp + 7 days, 1);
    }

    // ============================================================ repay (V4: borrower EOA direct)

    function test_Repay_FromBorrowerEoa_PullsUsdce() public {
        uint256 loanId = _openLoanV4(PRINCIPAL);

        vm.warp(block.timestamp + 30 days);
        (uint256 totalDebt,) = vault.debtOf(loanId);
        usdce.mint(alice, totalDebt - PRINCIPAL);

        vm.startPrank(alice);
        usdce.approve(address(vault), totalDebt);
        vault.repay(loanId);
        vm.stopPrank();

        // CTF returned to original collateralSource.
        assertEq(ctf.balanceOf(address(aliceSafe), CTF_ID), ALICE_SHARES, "CTF returned");
        // LP credited with full debt.
        assertEq(vault.lpPoolBalance(), LP_SEED - PRINCIPAL + totalDebt, "lpPool += totalDebt");

        ( , , , , , , , , bool active, bool repaid, , ) = vault.loans(loanId);
        assertFalse(active);
        assertTrue(repaid);
    }

    function test_Repay_RevertsWrongCaller() public {
        uint256 loanId = _openLoanV4(PRINCIPAL);

        // Mallory tries to repay alice's loan.
        address mallory = address(0xBAD);
        vm.prank(mallory);
        vm.expectRevert(LendingVaultV4.NotBorrower.selector);
        vault.repay(loanId);
    }

    function test_Repay_RevertsWhenAlreadyWithdrawn() public {
        uint256 loanId = _openLoanV4(PRINCIPAL);

        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        vm.prank(alice);
        vm.expectRevert(LendingVaultV4.LoanAlreadyWithdrawn.selector);
        vault.repay(loanId);
    }

    // ============================================================ V3 → V4 storage compat

    /// Spin up a V3 proxy with state, upgrade impl in place to V4, verify
    /// every state field still reads identically (including the V3 ERC-7201
    /// fields that V4 keeps for settleLiquidation).
    struct StateSnapshot {
        address borrower;
        address src;
        uint256 principal;
        uint256 lpPool;
        address treasury;
        address admin;
        address keeper;
        address usdc;
        address pUSD;
        address onramp;
        address offramp;
        bool active;
    }

    function test_Upgrade_V3ToV4_PreservesAllStorage() public {
        // Stage 1: deploy fresh V3 proxy + open a loan + run V3 init.
        MockUSDC nativeUsdc = new MockUSDC();
        LendingVaultV3 v3impl = new LendingVaultV3();
        bytes memory v3InitData = abi.encodeCall(
            LendingVaultV3.initialize,
            (
                IERC20(address(nativeUsdc)),
                IERC1155(address(ctf)),
                IPolymarketExchange(address(ex)),
                admin,
                keeper,
                upgrader
            )
        );
        ERC1967Proxy proxy = new ERC1967Proxy(address(v3impl), v3InitData);
        LendingVaultV3 v3 = LendingVaultV3(address(proxy));

        // Wire V3 + seed LP + open a V3 loan (uses 7-arg openLoan).
        vm.prank(admin);
        v3.initializeV3(IERC20(address(usdce)), address(pusd), address(onramp), address(offramp));

        vm.prank(admin);
        v3.setTreasury(treasury);

        usdce.mint(admin, LP_SEED);
        vm.startPrank(admin);
        usdce.approve(address(v3), LP_SEED);
        v3.depositLP(LP_SEED);
        vm.stopPrank();

        MockSafe safeV3 = new MockSafe(alice);
        ctf.mint(address(safeV3), 999, ALICE_SHARES);
        safeV3.setApprovalForAll1155(IERC1155(address(ctf)), address(v3), true);

        MockSafe aliceDeposit = new MockSafe(alice);
        vm.prank(alice);
        uint256 loanId = v3.openLoan(
            address(safeV3), 999, ALICE_SHARES, PRINCIPAL,
            block.timestamp + 7 days, 1, address(aliceDeposit)
        );

        // Snapshot.
        StateSnapshot memory snap = _snapshotV3(v3, loanId);

        // Stage 2: upgrade in place to V4 (no init call needed).
        LendingVaultV4 v4impl = new LendingVaultV4();
        vm.prank(upgrader);
        v3.upgradeToAndCall(address(v4impl), "");

        LendingVaultV4 v4 = LendingVaultV4(address(proxy));

        // Stage 3: every field still readable + identical.
        _assertSnapshotMatches(v4, loanId, snap);
    }

    function _snapshotV3(LendingVaultV3 v3, uint256 loanId)
        internal
        view
        returns (StateSnapshot memory s)
    {
        ( s.borrower, s.src, , , s.principal, , , , s.active, , , ) = v3.loans(loanId);
        s.lpPool = v3.lpPoolBalance();
        s.treasury = v3.treasury();
        s.admin = v3.admin();
        s.keeper = v3.keeper();
        s.usdc = address(v3.usdc());
        s.pUSD = v3.pUSD();
        s.onramp = v3.onramp();
        s.offramp = v3.offramp();
    }

    function _assertSnapshotMatches(LendingVaultV4 v4, uint256 loanId, StateSnapshot memory s)
        internal
        view
    {
        ( address b, address sr, , , uint256 p, , , , bool a, , , ) = v4.loans(loanId);
        require(b == s.borrower, "borrower drift");
        require(sr == s.src, "src drift");
        require(p == s.principal, "principal drift");
        require(a == s.active, "active drift");
        require(v4.lpPoolBalance() == s.lpPool, "lpPool drift");
        require(v4.treasury() == s.treasury, "treasury drift");
        require(v4.admin() == s.admin, "admin drift");
        require(v4.keeper() == s.keeper, "keeper drift");
        require(address(v4.usdc()) == s.usdc, "usdc drift");
        // V3 ERC-7201 fields still readable on V4.
        require(v4.pUSD() == s.pUSD, "pUSD drift");
        require(v4.onramp() == s.onramp, "onramp drift");
        require(v4.offramp() == s.offramp, "offramp drift");
    }

    // ============================================================ settleLiquidation (V3 behavior preserved)

    /// Smoke-test the V3 settle path on V4 — keeper pre-transfers pUSD,
    /// vault unwraps to USDC.e, distributes per the V2 waterfall.
    function test_Settle_PusdUnwrappedAndDistributed() public {
        uint256 priceE6 = 400_000;
        uint256 loanId = _openLoanV4(PRINCIPAL);

        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, priceE6);

        uint256 actualProceeds = (ALICE_SHARES * priceE6) / 1_000_000;
        pusd.mint(address(vault), actualProceeds);

        vm.warp(block.timestamp + 30 days);
        uint256 lpBefore = vault.lpPoolBalance();
        uint256 vaultPusdBefore = pusd.balanceOf(address(vault));

        vm.prank(keeper);
        vault.settleLiquidation(loanId, "ltv_breach", actualProceeds);

        // pUSD unwrapped fully.
        assertEq(vaultPusdBefore - pusd.balanceOf(address(vault)), actualProceeds);
        // LP got at least principal.
        assertGe(vault.lpPoolBalance() - lpBefore, PRINCIPAL);

        ( , , , , , , , , bool active, , bool liquidated, ) = vault.loans(loanId);
        assertFalse(active);
        assertTrue(liquidated);
    }
}
