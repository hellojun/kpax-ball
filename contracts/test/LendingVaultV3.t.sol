// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";

import {LendingVaultV2} from "../src/LendingVaultV2.sol";
import {LendingVaultV3} from "../src/LendingVaultV3.sol";
import {IPolymarketExchange} from "../src/interfaces/IPolymarketExchange.sol";

import {MockUSDC} from "./mocks/MockUSDC.sol";
import {MockCTF} from "./mocks/MockCTF.sol";
import {MockExchange} from "./mocks/MockExchange.sol";
import {MockSafe} from "./mocks/MockSafe.sol";
import {MockOnramp, MockOfframp} from "./mocks/MockCollateralRamp.sol";

/// @notice Sprint 5 V3 test suite. Covers:
///         - V3 init flow: switches `usdc` to USDC.e, wires ramps, max-approves.
///         - openLoan(7-arg): pUSD lands in borrower's V2 DepositWallet.
///         - repay: pUSD pulled from proxy, unwrapped, LP credited.
///         - settleLiquidation: pUSD unwrapped to USDC.e, V2 waterfall.
///         - V2→V3 upgrade preserves all V2 storage byte-for-byte.
contract LendingVaultV3Test is Test {
    MockUSDC usdce;          // V3 underlying (was Native USDC in V2)
    MockUSDC pusd;           // PM V2 collateral
    MockCTF ctf;
    MockExchange ex;
    MockOnramp onramp;
    MockOfframp offramp;

    LendingVaultV3 vault; // proxy, casted to V3 ABI

    address admin = address(0xA11CE);
    address keeper = address(0xBEEF);
    address upgrader = address(0xDA0DA0);
    address treasury = address(0xCB7A);

    address alice = address(0xA1);
    MockSafe aliceSafe;          // legacy proxy holding CTF (collateralSource)
    MockSafe aliceDepositWallet; // V2 DepositWallet (borrowerProxy + repay caller)

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

        // Deploy V3 implementation + ERC1967 proxy with the standard initialize.
        // initializeV3 is called separately to mirror the V2→V3 upgrade flow.
        LendingVaultV3 impl = new LendingVaultV3();
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
        ERC1967Proxy proxy = new ERC1967Proxy(address(impl), initData);
        vault = LendingVaultV3(address(proxy));

        // Run V3 init.
        vm.prank(admin);
        // initializeV3 is reinitializer(2) and is permissionless (no owner gate
        // — we trust the upgradeToAndCall caller). For tests we just call directly.
        vault.initializeV3(IERC20(address(usdce)), address(pusd), address(onramp), address(offramp));

        // Treasury must be set before any settle path runs.
        vm.prank(admin);
        vault.setTreasury(treasury);

        // LP pool seeded with USDC.e.
        usdce.mint(admin, LP_SEED);
        vm.startPrank(admin);
        usdce.approve(address(vault), LP_SEED);
        vault.depositLP(LP_SEED);
        vm.stopPrank();

        // Alice's legacy Safe-backed proxy holds her CTF.
        aliceSafe = new MockSafe(alice);
        ctf.mint(address(aliceSafe), CTF_ID, ALICE_SHARES);
        aliceSafe.setApprovalForAll1155(IERC1155(address(ctf)), address(vault), true);

        // Alice's V2 DepositWallet — destination for pUSD; caller for repay.
        aliceDepositWallet = new MockSafe(alice);

        // Keeper EOA must approve vault to pull CTF back during error-recovery.
        vm.prank(keeper);
        ctf.setApprovalForAll(address(vault), true);
    }

    // ============================================================ initializeV3

    function test_InitializeV3_SwapsUsdcAndApprovesRamps() public {
        assertEq(address(vault.usdc()), address(usdce), "usdc switched to usdce");
        assertEq(vault.pUSD(), address(pusd));
        assertEq(vault.onramp(), address(onramp));
        assertEq(vault.offramp(), address(offramp));
        assertEq(usdce.allowance(address(vault), address(onramp)), type(uint256).max);
        assertEq(pusd.allowance(address(vault), address(offramp)), type(uint256).max);
    }

    function test_InitializeV3_NotCallableTwice() public {
        // reinitializer(2) blocks a second invocation.
        vm.expectRevert();
        vault.initializeV3(IERC20(address(usdce)), address(pusd), address(onramp), address(offramp));
    }

    function test_InitializeV3_RevertsZeroAddress() public {
        // Spin up a fresh vault to test rejection.
        LendingVaultV3 impl = new LendingVaultV3();
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
        ERC1967Proxy proxy = new ERC1967Proxy(address(impl), initData);
        LendingVaultV3 fresh = LendingVaultV3(address(proxy));

        vm.expectRevert(LendingVaultV3.ZeroAddress.selector);
        fresh.initializeV3(IERC20(address(0)), address(pusd), address(onramp), address(offramp));
    }

    // ============================================================ openLoan (V3)

    function _openLoanV3(uint256 principal) internal returns (uint256 loanId) {
        vm.prank(alice);
        loanId = vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            principal,
            block.timestamp + 7 days,
            1,
            address(aliceDepositWallet)
        );
    }

    function test_OpenLoan_PusdLandsInBorrowerProxy() public {
        uint256 pusdBefore = pusd.balanceOf(address(aliceDepositWallet));

        uint256 loanId = _openLoanV3(PRINCIPAL);

        assertEq(
            pusd.balanceOf(address(aliceDepositWallet)) - pusdBefore,
            PRINCIPAL,
            "pusd minted into borrowerProxy"
        );
        // Borrower EOA never sees usdce / pusd — they only ever hold CTF.
        assertEq(usdce.balanceOf(alice), 0, "alice EOA stays empty");
        assertEq(pusd.balanceOf(alice), 0, "alice EOA stays empty");
        // CTF moved into vault.
        assertEq(ctf.balanceOf(address(vault), CTF_ID), ALICE_SHARES, "CTF in vault");
        // Vault's USDC.e shrunk by principal (Onramp pulled it).
        assertEq(usdce.balanceOf(address(vault)), LP_SEED - PRINCIPAL, "vault usdce drained by principal");
        // borrowerProxy registered for repay path.
        assertEq(vault.borrowerProxyOf(loanId), address(aliceDepositWallet), "borrowerProxy stored");
    }

    function test_OpenLoan_RevertsBorrowerProxyZero() public {
        vm.prank(alice);
        vm.expectRevert(LendingVaultV3.BorrowerProxyZero.selector);
        vault.openLoan(
            address(aliceSafe), CTF_ID, ALICE_SHARES, PRINCIPAL,
            block.timestamp + 7 days, 1, address(0)
        );
    }

    function test_OpenLoan_RevertsCollateralSourceZero() public {
        vm.prank(alice);
        vm.expectRevert(LendingVaultV3.CollateralSourceZero.selector);
        vault.openLoan(
            address(0), CTF_ID, ALICE_SHARES, PRINCIPAL,
            block.timestamp + 7 days, 1, address(aliceDepositWallet)
        );
    }

    function test_OpenLoan_RevertsNotProxyOwner() public {
        // mallory tries to borrow against alice's proxy
        address mallory = address(0xBAD);
        vm.prank(mallory);
        vm.expectRevert(LendingVaultV3.NotProxyOwner.selector);
        vault.openLoan(
            address(aliceSafe), CTF_ID, ALICE_SHARES, PRINCIPAL,
            block.timestamp + 7 days, 1, address(aliceDepositWallet)
        );
    }

    // ============================================================ repay (V3)

    function test_Repay_FromBorrowerProxy_PullsPusdAndUnwraps() public {
        uint256 loanId = _openLoanV3(PRINCIPAL);

        // Warp 30 days to accrue interest.
        vm.warp(block.timestamp + 30 days);
        (uint256 totalDebt,) = vault.debtOf(loanId);
        // Mint extra pUSD to proxy to cover interest.
        pusd.mint(address(aliceDepositWallet), totalDebt - PRINCIPAL);

        // Proxy approves vault to pull pUSD.
        aliceDepositWallet.runTx(
            address(pusd),
            0,
            abi.encodeWithSignature("approve(address,uint256)", address(vault), totalDebt)
        );

        uint256 lpBefore = vault.lpPoolBalance();
        uint256 vaultUsdceBefore = usdce.balanceOf(address(vault));
        uint256 proxyPusdBefore = pusd.balanceOf(address(aliceDepositWallet));
        uint256 proxyCtfBefore = ctf.balanceOf(address(aliceSafe), CTF_ID);

        // Proxy calls vault.repay(loanId).
        aliceDepositWallet.runTx(
            address(vault),
            0,
            abi.encodeWithSignature("repay(uint256)", loanId)
        );

        // pUSD left the proxy.
        assertEq(proxyPusdBefore - pusd.balanceOf(address(aliceDepositWallet)), totalDebt, "pusd pulled from proxy");
        // Vault's USDC.e grew by totalDebt (offramp minted it on unwrap).
        assertEq(usdce.balanceOf(address(vault)) - vaultUsdceBefore, totalDebt, "usdce minted into vault");
        // LP pool credited with totalDebt.
        assertEq(vault.lpPoolBalance() - lpBefore, totalDebt, "lpPool += totalDebt");
        // CTF returned to original collateralSource (legacy Safe).
        assertEq(
            ctf.balanceOf(address(aliceSafe), CTF_ID) - proxyCtfBefore,
            ALICE_SHARES,
            "CTF returned to legacy Safe"
        );
    }

    function test_Repay_RevertsWrongCaller() public {
        uint256 loanId = _openLoanV3(PRINCIPAL);

        // Borrower EOA tries to repay directly — wrong caller, should be the proxy.
        vm.prank(alice);
        vm.expectRevert(LendingVaultV3.WrongRepayCaller.selector);
        vault.repay(loanId);
    }

    function test_Repay_RevertsWhenLoanInactive() public {
        // Non-existent loanId. Prank as the proxy address directly so the
        // revert bubbles through cleanly (MockSafe.runTx swallows reasons).
        vm.prank(address(aliceDepositWallet));
        vm.expectRevert(LendingVaultV3.LoanInactive.selector);
        vault.repay(99);
    }

    function test_Repay_RevertsWhenAlreadyWithdrawn() public {
        uint256 loanId = _openLoanV3(PRINCIPAL);
        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        vm.prank(address(aliceDepositWallet));
        vm.expectRevert(LendingVaultV3.LoanAlreadyWithdrawn.selector);
        vault.repay(loanId);
    }

    // ============================================================ settleLiquidation (V3)

    function test_Settle_HappyPath_PusdUnwrappedAndDistributed() public {
        uint256 priceE6 = 400_000;
        uint256 loanId = _openLoanV3(PRINCIPAL);

        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, priceE6);

        uint256 actualProceeds = (ALICE_SHARES * priceE6) / 1_000_000; // 400e6
        // Simulate keeper's step 4: pUSD pre-deposited into vault (via PM Relayer).
        pusd.mint(address(vault), actualProceeds);

        // Warp 30d for nontrivial interest.
        vm.warp(block.timestamp + 30 days);
        (, uint256 interest) = vault.debtOf(loanId);
        uint256 penalty = (actualProceeds * 200) / 10_000;
        uint256 expectedToLp = PRINCIPAL;
        uint256 remaining = actualProceeds - expectedToLp;
        uint256 treasuryDue = interest + penalty;
        uint256 expectedToTreasury = remaining < treasuryDue ? remaining : treasuryDue;
        uint256 expectedResidual = remaining - expectedToTreasury;
        assertGt(expectedResidual, 0, "branch precondition: residual > 0");

        uint256 lpBefore = vault.lpPoolBalance();
        uint256 treasuryBefore = usdce.balanceOf(treasury);
        uint256 borrowerBefore = usdce.balanceOf(alice);
        uint256 vaultPusdBefore = pusd.balanceOf(address(vault));

        vm.prank(keeper);
        vault.settleLiquidation(loanId, "ltv_breach", actualProceeds);

        // pUSD was unwrapped — vault's pUSD balance dropped.
        assertEq(
            vaultPusdBefore - pusd.balanceOf(address(vault)),
            actualProceeds,
            "pUSD unwrapped"
        );
        // Distributions happened in USDC.e.
        assertEq(vault.lpPoolBalance() - lpBefore, expectedToLp, "LP got principal");
        assertEq(usdce.balanceOf(treasury) - treasuryBefore, expectedToTreasury, "treasury got int+pen");
        assertEq(usdce.balanceOf(alice) - borrowerBefore, expectedResidual, "borrower got residual");

        ( , , , , , , , , bool active, , bool liquidated, ) = vault.loans(loanId);
        assertFalse(active);
        assertTrue(liquidated);
    }

    function test_Settle_RevertsOnInsufficientPusd() public {
        uint256 loanId = _openLoanV3(PRINCIPAL);
        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        // Vault holds 0 pUSD — keeper claims actualProceeds=100e6.
        vm.prank(keeper);
        vm.expectRevert(LendingVaultV3.InsufficientProceeds.selector);
        vault.settleLiquidation(loanId, "ltv_breach", 100e6);
    }

    function test_Settle_UnderRecovery_LpEatsLoss() public {
        uint256 loanId = _openLoanV3(PRINCIPAL);
        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        uint256 actualProceeds = 100e6; // < 250e6 principal
        pusd.mint(address(vault), actualProceeds);

        uint256 lpBefore = vault.lpPoolBalance();
        uint256 treasuryBefore = usdce.balanceOf(treasury);
        uint256 borrowerBefore = usdce.balanceOf(alice);

        vm.prank(keeper);
        vault.settleLiquidation(loanId, "ltv_breach", actualProceeds);

        assertEq(vault.lpPoolBalance() - lpBefore, actualProceeds, "LP got proceeds");
        assertEq(usdce.balanceOf(treasury), treasuryBefore, "treasury got nothing");
        assertEq(usdce.balanceOf(alice), borrowerBefore, "borrower got nothing");
    }

    // ============================================================ LP regression

    function test_LpFlow_DepositWithdrawUSDCE_NoChange() public {
        uint256 amount = 500e6;
        usdce.mint(alice, amount);

        vm.startPrank(alice);
        usdce.approve(address(vault), amount);
        vault.depositLP(amount);
        vm.stopPrank();

        uint256 lpAfterDeposit = vault.lpPoolBalance();
        assertEq(lpAfterDeposit, LP_SEED + amount);

        vm.prank(admin);
        vault.withdrawLP(admin, amount);

        assertEq(vault.lpPoolBalance(), LP_SEED);
        assertEq(usdce.balanceOf(admin), amount, "admin got usdce");
    }

    // ============================================================ V2→V3 upgrade storage compat

    /// @notice Spins up V2 + state, upgrades proxy in place, verifies storage.
    ///         Helpers below to keep stack frames small (Yul "stack too deep").
    struct V2Snapshot {
        address borrower;
        address src;
        uint256 token;
        uint256 shares;
        uint256 principal;
        uint256 openTime;
        uint256 kickoff;
        uint8 tier;
        bool active;
        bool repaid;
        bool liq;
        bool withdrawn;
        uint256 lpPool;
        address treasury;
        address admin;
        address keeper;
        address upgrader;
        uint256 nextLoanId;
        bool paused;
        address usdc;
        address ctf;
    }

    function _spinUpV2(MockUSDC nativeUsdc) internal returns (LendingVaultV2 v2, address proxyAddr, uint256 loanId) {
        LendingVaultV2 v2impl = new LendingVaultV2();
        bytes memory v2InitData = abi.encodeCall(
            LendingVaultV2.initialize,
            (
                IERC20(address(nativeUsdc)),
                IERC1155(address(ctf)),
                IPolymarketExchange(address(ex)),
                admin,
                keeper,
                upgrader
            )
        );
        ERC1967Proxy proxy = new ERC1967Proxy(address(v2impl), v2InitData);
        proxyAddr = address(proxy);
        v2 = LendingVaultV2(proxyAddr);

        vm.prank(admin);
        v2.setTreasury(treasury);

        nativeUsdc.mint(admin, LP_SEED);
        vm.startPrank(admin);
        nativeUsdc.approve(address(v2), LP_SEED);
        v2.depositLP(LP_SEED);
        vm.stopPrank();

        MockSafe v2Safe = new MockSafe(alice);
        ctf.mint(address(v2Safe), CTF_ID, ALICE_SHARES);
        v2Safe.setApprovalForAll1155(IERC1155(address(ctf)), address(v2), true);
        vm.prank(keeper);
        ctf.setApprovalForAll(address(v2), true);

        vm.prank(alice);
        loanId = v2.openLoan(
            address(v2Safe), CTF_ID, ALICE_SHARES, PRINCIPAL,
            block.timestamp + 7 days, 2
        );
    }

    function _snapshotV2(LendingVaultV2 v2, uint256 loanId) internal view returns (V2Snapshot memory s) {
        (
            s.borrower, s.src, s.token, s.shares,
            s.principal, s.openTime, s.kickoff, s.tier,
            s.active, s.repaid, s.liq, s.withdrawn
        ) = v2.loans(loanId);
        s.lpPool = v2.lpPoolBalance();
        s.treasury = v2.treasury();
        s.admin = v2.admin();
        s.keeper = v2.keeper();
        s.upgrader = v2.upgrader();
        s.nextLoanId = v2.nextLoanId();
        s.paused = v2.paused();
        s.usdc = address(v2.usdc());
        s.ctf = address(v2.ctf());
    }

    function _assertSnapshotMatches(LendingVaultV3 v3, uint256 loanId, V2Snapshot memory s) internal view {
        (
            address aBorrower, address aSrc, uint256 aToken, uint256 aShares,
            uint256 aPrincipal, uint256 aOpenTime, uint256 aKickoff, uint8 aTier,
            bool aActive, bool aRepaid, bool aLiq, bool aWithdrawn
        ) = v3.loans(loanId);

        require(aBorrower == s.borrower, "borrower drift");
        require(aSrc == s.src, "src drift");
        require(aToken == s.token, "token drift");
        require(aShares == s.shares, "shares drift");
        require(aPrincipal == s.principal, "principal drift");
        require(aOpenTime == s.openTime, "openTime drift");
        require(aKickoff == s.kickoff, "kickoff drift");
        require(aTier == s.tier, "tier drift");
        require(aActive == s.active, "active drift");
        require(aRepaid == s.repaid, "repaid drift");
        require(aLiq == s.liq, "liq drift");
        require(aWithdrawn == s.withdrawn, "withdrawn drift");
        require(v3.lpPoolBalance() == s.lpPool, "lpPool drift");
        require(v3.treasury() == s.treasury, "treasury drift");
        require(v3.admin() == s.admin, "admin drift");
        require(v3.keeper() == s.keeper, "keeper drift");
        require(v3.upgrader() == s.upgrader, "upgrader drift");
        require(v3.nextLoanId() == s.nextLoanId, "nextLoanId drift");
        require(v3.paused() == s.paused, "paused drift");
        require(address(v3.ctf()) == s.ctf, "ctf drift");
    }

    function test_Upgrade_V2ToV3_PreservesAllStorage() public {
        MockUSDC nativeUsdc = new MockUSDC();
        (LendingVaultV2 v2, address proxyAddr, uint256 loanId) = _spinUpV2(nativeUsdc);
        V2Snapshot memory snap = _snapshotV2(v2, loanId);

        // Upgrade proxy in place + run initializeV3 atomically.
        LendingVaultV3 v3impl = new LendingVaultV3();
        bytes memory v3InitCall = abi.encodeCall(
            LendingVaultV3.initializeV3,
            (IERC20(address(usdce)), address(pusd), address(onramp), address(offramp))
        );
        vm.prank(upgrader);
        v2.upgradeToAndCall(address(v3impl), v3InitCall);

        LendingVaultV3 v3 = LendingVaultV3(proxyAddr);

        _assertSnapshotMatches(v3, loanId, snap);

        // V3 init swapped the underlying token.
        assertNotEq(address(v3.usdc()), snap.usdc, "usdc swapped from native to usdce");
        assertEq(address(v3.usdc()), address(usdce), "usdc is now usdce");

        // V3 storage wired correctly.
        assertEq(v3.pUSD(), address(pusd));
        assertEq(v3.onramp(), address(onramp));
        assertEq(v3.offramp(), address(offramp));
        assertEq(usdce.allowance(address(v3), address(onramp)), type(uint256).max);
        assertEq(pusd.allowance(address(v3), address(offramp)), type(uint256).max);

        // Legacy V2 loan has no borrowerProxy registered (zero default).
        assertEq(v3.borrowerProxyOf(loanId), address(0), "legacy loan has no borrowerProxy");
    }
}
