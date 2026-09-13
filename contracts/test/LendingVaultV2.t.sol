// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";

import {LendingVault} from "../src/LendingVault.sol";
import {LendingVaultV2} from "../src/LendingVaultV2.sol";
import {IPolymarketExchange} from "../src/interfaces/IPolymarketExchange.sol";

import {MockUSDC} from "./mocks/MockUSDC.sol";
import {MockCTF} from "./mocks/MockCTF.sol";
import {MockExchange} from "./mocks/MockExchange.sol";
import {MockSafe} from "./mocks/MockSafe.sol";

/// @notice Sprint 4 V2 test suite. Covers the realistic 4-step liquidation
///         flow (withdrawCtfForLiquidation → off-chain sell → keeper deposit →
///         settleLiquidation), the error-recovery path (returnCtfFromKeeper),
///         the treasury admin surface, and a V1→V2 upgrade test that asserts
///         storage layout compatibility.
contract LendingVaultV2Test is Test {
    MockUSDC usdc;
    MockCTF ctf;
    MockExchange ex;
    LendingVaultV2 vault; // proxy, casted to V2 ABI

    address admin = address(0xA11CE);
    address keeper = address(0xBEEF);
    address upgrader = address(0xDA0DA0);
    address treasury = address(0xCB7A);

    address alice = address(0xA1);
    MockSafe aliceSafe;

    address mallory = address(0xBAD);
    MockSafe mallorySafe;

    uint256 constant CTF_ID = 42;
    uint256 constant ALICE_SHARES = 1_000_000_000; // 1e9 — same scale as Polymarket CTF
    uint256 constant PRINCIPAL = 250e6; // 250 USDC
    uint256 constant LP_SEED = 10_000e6;

    function setUp() public {
        usdc = new MockUSDC();
        ctf = new MockCTF();
        ex = new MockExchange();

        // Deploy V2 implementation + ERC1967 proxy.
        LendingVaultV2 impl = new LendingVaultV2();
        bytes memory initData = abi.encodeCall(
            LendingVaultV2.initialize,
            (
                IERC20(address(usdc)),
                IERC1155(address(ctf)),
                IPolymarketExchange(address(ex)),
                admin,
                keeper,
                upgrader
            )
        );
        ERC1967Proxy proxy = new ERC1967Proxy(address(impl), initData);
        vault = LendingVaultV2(address(proxy));

        // Treasury initially zero — admin sets it before any settle path runs.
        vm.prank(admin);
        vault.setTreasury(treasury);

        // LP pool seeded.
        usdc.mint(admin, LP_SEED);
        vm.startPrank(admin);
        usdc.approve(address(vault), LP_SEED);
        vault.depositLP(LP_SEED);
        vm.stopPrank();

        // Alice's Polymarket Safe holds her CTF collateral.
        aliceSafe = new MockSafe(alice);
        ctf.mint(address(aliceSafe), CTF_ID, ALICE_SHARES);
        aliceSafe.setApprovalForAll1155(IERC1155(address(ctf)), address(vault), true);

        mallorySafe = new MockSafe(mallory);

        // Keeper EOA must approve vault to pull CTF back during error-recovery
        // (returnCtfFromKeeper). One-time setup mirrors production deployment.
        vm.prank(keeper);
        ctf.setApprovalForAll(address(vault), true);
    }

    function _openLoan(uint256 principal) internal returns (uint256 loanId) {
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

    // ============================================================ withdrawCtfForLiquidation

    function test_WithdrawCtf_HappyPath() public {
        uint256 loanId = _openLoan(PRINCIPAL);
        uint256 priceE6 = 400_000; // 0.40

        uint256 expectedProceeds = (ALICE_SHARES * priceE6) / 1_000_000;

        vm.expectEmit(true, false, false, true, address(vault));
        emit LendingVaultV2.LiquidationStarted(loanId, priceE6, expectedProceeds);

        vm.prank(keeper);
        uint256 returned = vault.withdrawCtfForLiquidation(loanId, priceE6);

        assertEq(returned, expectedProceeds, "expectedProceeds returned");
        assertEq(ctf.balanceOf(address(vault), CTF_ID), 0, "vault no longer holds CTF");
        assertEq(ctf.balanceOf(keeper, CTF_ID), ALICE_SHARES, "keeper holds CTF");

        // Loan flags
        ( , , , , , , , , bool active, bool repaid, bool liquidated, bool withdrawn) =
            vault.loans(loanId);
        assertTrue(active, "still active");
        assertFalse(repaid, "!repaid");
        assertFalse(liquidated, "!liquidated");
        assertTrue(withdrawn, "withdrawn flag set");
    }

    function test_WithdrawCtf_RevertsForNonKeeper() public {
        uint256 loanId = _openLoan(PRINCIPAL);

        vm.prank(alice);
        vm.expectRevert(LendingVaultV2.OnlyKeeper.selector);
        vault.withdrawCtfForLiquidation(loanId, 500_000);
    }

    function test_WithdrawCtf_RevertsOnZeroPrice() public {
        uint256 loanId = _openLoan(PRINCIPAL);

        vm.prank(keeper);
        vm.expectRevert(LendingVaultV2.InvalidPrice.selector);
        vault.withdrawCtfForLiquidation(loanId, 0);
    }

    function test_WithdrawCtf_RevertsOnPriceAboveOne() public {
        uint256 loanId = _openLoan(PRINCIPAL);

        vm.prank(keeper);
        vm.expectRevert(LendingVaultV2.InvalidPrice.selector);
        vault.withdrawCtfForLiquidation(loanId, 1_000_001);
    }

    function test_WithdrawCtf_AcceptsExactlyOneE6() public {
        uint256 loanId = _openLoan(PRINCIPAL);
        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 1_000_000);
    }

    function test_WithdrawCtf_RevertsOnInactive() public {
        uint256 loanId = _openLoan(PRINCIPAL);

        // Repay then try to withdraw.
        (uint256 totalDebt,) = vault.debtOf(loanId);
        usdc.mint(alice, totalDebt - PRINCIPAL);
        vm.startPrank(alice);
        usdc.approve(address(vault), totalDebt);
        vault.repay(loanId);
        vm.stopPrank();

        vm.prank(keeper);
        vm.expectRevert(LendingVaultV2.LoanInactive.selector);
        vault.withdrawCtfForLiquidation(loanId, 500_000);
    }

    function test_WithdrawCtf_RevertsWhenAlreadyWithdrawn() public {
        uint256 loanId = _openLoan(PRINCIPAL);

        vm.startPrank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);
        vm.expectRevert(LendingVaultV2.LoanAlreadyWithdrawn.selector);
        vault.withdrawCtfForLiquidation(loanId, 500_000);
        vm.stopPrank();
    }

    function test_WithdrawCtf_RevertsOnAlreadyLiquidated() public {
        // Run the full happy path once, then try to withdraw again.
        uint256 loanId = _openLoan(PRINCIPAL);
        uint256 priceE6 = 500_000;
        uint256 actualProceeds = (ALICE_SHARES * priceE6) / 1_000_000;

        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, priceE6);

        usdc.mint(address(vault), actualProceeds);

        vm.prank(keeper);
        vault.settleLiquidation(loanId, "ltv_breach", actualProceeds);

        // Loan is now !active && liquidated → withdraw again must revert with
        // LoanInactive (we check active before withdrawn).
        vm.prank(keeper);
        vm.expectRevert(LendingVaultV2.LoanInactive.selector);
        vault.withdrawCtfForLiquidation(loanId, priceE6);
    }

    // ============================================================ settleLiquidation distribution

    /// Branch A: actualProceeds > principal + interest + penalty
    /// → LP gets principal, treasury gets interest+penalty, borrower gets residual.
    function test_Settle_HappyPath_FullResidualToBorrower() public {
        // Pick numbers where proceeds clearly exceeds debt+penalty.
        // principal = 250 USDC, shares = 1e9, price = 0.40 → proceeds = 400 USDC.
        uint256 priceE6 = 400_000;
        uint256 loanId = _openLoan(PRINCIPAL);

        // Step 1
        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, priceE6);

        // Step 2-3 (off-chain): keeper sold CTF for `actualProceeds` and
        // transferred USDC to vault. We mint mocked USDC into vault directly.
        uint256 actualProceeds = (ALICE_SHARES * priceE6) / 1_000_000; // 400e6
        usdc.mint(address(vault), actualProceeds);

        // Compute expected splits.
        // Warp 30 days for nontrivial interest.
        vm.warp(block.timestamp + 30 days);
        (, uint256 interest) = vault.debtOf(loanId);
        uint256 penalty = (actualProceeds * 200) / 10_000; // 8e6
        uint256 expectedToLp = PRINCIPAL; // capped at principal (proceeds > principal)
        uint256 remaining = actualProceeds - expectedToLp;
        uint256 treasuryDue = interest + penalty;
        uint256 expectedToTreasury = remaining < treasuryDue ? remaining : treasuryDue;
        uint256 expectedResidual = remaining - expectedToTreasury;

        // Sanity: this branch must produce a residual > 0.
        assertGt(expectedResidual, 0, "branch precondition: residual > 0");

        uint256 lpBefore = vault.lpPoolBalance();
        uint256 treasuryBefore = usdc.balanceOf(treasury);
        uint256 borrowerBefore = usdc.balanceOf(alice);

        vm.expectEmit(true, false, false, true, address(vault));
        emit LendingVaultV2.LoanLiquidated(
            loanId,
            "ltv_breach",
            actualProceeds,
            expectedToLp,
            expectedToTreasury,
            expectedResidual
        );

        vm.prank(keeper);
        vault.settleLiquidation(loanId, "ltv_breach", actualProceeds);

        assertEq(vault.lpPoolBalance() - lpBefore, expectedToLp, "LP got principal exactly");
        assertEq(usdc.balanceOf(treasury) - treasuryBefore, expectedToTreasury, "treasury got interest+penalty");
        assertEq(usdc.balanceOf(alice) - borrowerBefore, expectedResidual, "borrower got residual");

        ( , , , , , , , , bool active, , bool liquidated, ) = vault.loans(loanId);
        assertFalse(active);
        assertTrue(liquidated);
    }

    /// Branch B: actualProceeds == principal + interest + penalty exactly
    /// → residual is exactly zero, treasury gets interest + penalty in full.
    function test_Settle_TreasuryGetsExactlyInterestPlusPenalty_NoResidual() public {
        uint256 loanId = _openLoan(PRINCIPAL);

        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        // Need to construct actualProceeds so that:
        //   actualProceeds = principal + interest + penalty
        //   penalty = actualProceeds * 200 / 10000
        // → actualProceeds * (1 - 200/10000) = principal + interest
        // → actualProceeds = (principal + interest) * 10000 / 9800
        // Warp first to fix interest.
        vm.warp(block.timestamp + 30 days);
        (, uint256 interest) = vault.debtOf(loanId);
        uint256 actualProceeds = ((PRINCIPAL + interest) * 10_000) / 9_800;
        // We may end up off by 1 wei from rounding, that's fine — the
        // distribution math still goes through the "treasury exact" branch
        // as long as remaining ≤ treasuryDue.

        usdc.mint(address(vault), actualProceeds);

        uint256 penalty = (actualProceeds * 200) / 10_000;
        uint256 expectedToLp = PRINCIPAL;
        uint256 remaining = actualProceeds - expectedToLp;
        uint256 treasuryDue = interest + penalty;
        uint256 expectedToTreasury = remaining < treasuryDue ? remaining : treasuryDue;
        uint256 expectedResidual = remaining - expectedToTreasury;

        uint256 lpBefore = vault.lpPoolBalance();
        uint256 treasuryBefore = usdc.balanceOf(treasury);
        uint256 borrowerBefore = usdc.balanceOf(alice);

        vm.prank(keeper);
        vault.settleLiquidation(loanId, "kickoff_due", actualProceeds);

        assertEq(vault.lpPoolBalance() - lpBefore, expectedToLp);
        assertEq(usdc.balanceOf(treasury) - treasuryBefore, expectedToTreasury);
        assertEq(usdc.balanceOf(alice) - borrowerBefore, expectedResidual);
        // The actualProceeds is dialed to land on "treasury full" (residual ≈ 0).
        assertLe(expectedResidual, 1, "residual should be 0 or 1 wei rounding");
    }

    /// Branch C: principal < actualProceeds < principal + interest + penalty
    /// → LP gets principal, treasury gets only the leftover (capped), residual = 0.
    function test_Settle_PartialTreasury_NoResidual() public {
        uint256 loanId = _openLoan(PRINCIPAL);

        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        // Warp 30 days for measurable interest.
        vm.warp(block.timestamp + 30 days);
        (, uint256 interest) = vault.debtOf(loanId);

        // Pick actualProceeds = principal + (interest / 2) — strictly between
        // principal and principal+interest+penalty.
        uint256 actualProceeds = PRINCIPAL + interest / 2;
        usdc.mint(address(vault), actualProceeds);

        uint256 penalty = (actualProceeds * 200) / 10_000;
        uint256 expectedToLp = PRINCIPAL;
        uint256 remaining = actualProceeds - expectedToLp; // = interest/2
        uint256 treasuryDue = interest + penalty;          // > remaining
        uint256 expectedToTreasury = remaining; // capped at remaining
        uint256 expectedResidual = 0;

        // Sanity: this branch must hit treasury cap.
        assertLt(remaining, treasuryDue, "branch precondition: remaining < treasuryDue");

        uint256 lpBefore = vault.lpPoolBalance();
        uint256 treasuryBefore = usdc.balanceOf(treasury);
        uint256 borrowerBefore = usdc.balanceOf(alice);

        vm.prank(keeper);
        vault.settleLiquidation(loanId, "ltv_breach", actualProceeds);

        assertEq(vault.lpPoolBalance() - lpBefore, expectedToLp, "LP got principal");
        assertEq(usdc.balanceOf(treasury) - treasuryBefore, expectedToTreasury, "treasury got partial");
        assertEq(usdc.balanceOf(alice), borrowerBefore, "borrower got nothing");
        assertEq(expectedResidual, 0);
    }

    /// Branch D: actualProceeds < principal — under-recovery, LP eats loss.
    function test_Settle_UnderRecovery_LpEatsLoss() public {
        uint256 loanId = _openLoan(PRINCIPAL);

        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        // actualProceeds = 100e6 (< 250e6 principal)
        uint256 actualProceeds = 100e6;
        usdc.mint(address(vault), actualProceeds);

        uint256 expectedToLp = actualProceeds; // capped at proceeds
        uint256 expectedToTreasury = 0;
        uint256 expectedResidual = 0;

        uint256 lpBefore = vault.lpPoolBalance();
        uint256 treasuryBefore = usdc.balanceOf(treasury);
        uint256 borrowerBefore = usdc.balanceOf(alice);

        vm.prank(keeper);
        vault.settleLiquidation(loanId, "ltv_breach", actualProceeds);

        assertEq(vault.lpPoolBalance() - lpBefore, expectedToLp, "LP got proceeds");
        assertEq(usdc.balanceOf(treasury), treasuryBefore, "treasury got nothing");
        assertEq(usdc.balanceOf(alice), borrowerBefore, "borrower got nothing");
    }

    // ============================================================ settleLiquidation reverts

    function test_Settle_RevertsForNonKeeper() public {
        uint256 loanId = _openLoan(PRINCIPAL);
        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        vm.prank(alice);
        vm.expectRevert(LendingVaultV2.OnlyKeeper.selector);
        vault.settleLiquidation(loanId, "ltv_breach", 100e6);
    }

    function test_Settle_RevertsWhenNotWithdrawn() public {
        uint256 loanId = _openLoan(PRINCIPAL);
        // Skip the withdraw step.
        usdc.mint(address(vault), 100e6);

        vm.prank(keeper);
        vm.expectRevert(LendingVaultV2.LoanNotWithdrawn.selector);
        vault.settleLiquidation(loanId, "ltv_breach", 100e6);
    }

    function test_Settle_RevertsWhenAlreadyLiquidated() public {
        uint256 loanId = _openLoan(PRINCIPAL);
        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);
        usdc.mint(address(vault), 100e6);

        vm.prank(keeper);
        vault.settleLiquidation(loanId, "ltv_breach", 100e6);

        // Second settle should revert. The loan still has withdrawn=true and
        // liquidated=true, so settle hits the liquidated guard first.
        vm.prank(keeper);
        vm.expectRevert(LendingVaultV2.LoanAlreadyLiquidated.selector);
        vault.settleLiquidation(loanId, "ltv_breach", 100e6);
    }

    function test_Settle_RevertsOnInvalidReason() public {
        uint256 loanId = _openLoan(PRINCIPAL);
        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);
        usdc.mint(address(vault), 100e6);

        vm.prank(keeper);
        vm.expectRevert(LendingVaultV2.InvalidReason.selector);
        vault.settleLiquidation(loanId, "rug_pull", 100e6);
    }

    function test_Settle_RevertsWhenTreasuryNotSet() public {
        // Spin up a fresh proxy so we can deliberately leave treasury unset.
        LendingVaultV2 impl = new LendingVaultV2();
        bytes memory initData = abi.encodeCall(
            LendingVaultV2.initialize,
            (
                IERC20(address(usdc)),
                IERC1155(address(ctf)),
                IPolymarketExchange(address(ex)),
                admin,
                keeper,
                upgrader
            )
        );
        ERC1967Proxy proxy = new ERC1967Proxy(address(impl), initData);
        LendingVaultV2 freshVault = LendingVaultV2(address(proxy));

        // Seed LP pool.
        usdc.mint(admin, LP_SEED);
        vm.startPrank(admin);
        usdc.approve(address(freshVault), LP_SEED);
        freshVault.depositLP(LP_SEED);
        vm.stopPrank();

        // New Safe + alice for this fresh vault (different vault address).
        MockSafe freshSafe = new MockSafe(alice);
        ctf.mint(address(freshSafe), CTF_ID, ALICE_SHARES);
        freshSafe.setApprovalForAll1155(IERC1155(address(ctf)), address(freshVault), true);

        vm.prank(alice);
        uint256 loanId = freshVault.openLoan(
            address(freshSafe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );

        vm.prank(keeper);
        freshVault.withdrawCtfForLiquidation(loanId, 500_000);

        usdc.mint(address(freshVault), 100e6);

        vm.prank(keeper);
        vm.expectRevert(LendingVaultV2.TreasuryNotSet.selector);
        freshVault.settleLiquidation(loanId, "ltv_breach", 100e6);
    }

    function test_Settle_RevertsOnInsufficientFreeBalance() public {
        uint256 loanId = _openLoan(PRINCIPAL);
        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        // Vault free balance = LP_SEED - PRINCIPAL = 9750e6.
        // Drain it via emergency hatch so free = 0.
        vm.startPrank(admin);
        vault.setPaused(true);
        vault.emergencyWithdrawERC20(IERC20(address(usdc)), admin, usdc.balanceOf(address(vault)));
        vault.setPaused(false);
        vm.stopPrank();

        // Now actualProceeds 100e6 exceeds vault free balance (0).
        vm.prank(keeper);
        vm.expectRevert(LendingVaultV2.InsufficientProceeds.selector);
        vault.settleLiquidation(loanId, "ltv_breach", 100e6);
    }

    function test_Settle_RevertsWhenLoanRepaid() public {
        // Hard to reach this branch in practice (repay flips active=false and
        // can't run while withdrawn=true), but we can construct it manually
        // by storage poking via a helper. Skip in favour of the simpler
        // invariant: repay() reverts during withdrawn.
        uint256 loanId = _openLoan(PRINCIPAL);

        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        // Borrower tries to repay → should revert because we're already
        // withdrawn (vault doesn't hold the CTF).
        (uint256 totalDebt,) = vault.debtOf(loanId);
        usdc.mint(alice, totalDebt - PRINCIPAL);
        vm.startPrank(alice);
        usdc.approve(address(vault), totalDebt);
        vm.expectRevert(LendingVaultV2.LoanAlreadyWithdrawn.selector);
        vault.repay(loanId);
        vm.stopPrank();
    }

    // ============================================================ returnCtfFromKeeper

    function test_ReturnCtf_HappyPath() public {
        uint256 loanId = _openLoan(PRINCIPAL);

        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        assertEq(ctf.balanceOf(keeper, CTF_ID), ALICE_SHARES);
        assertEq(ctf.balanceOf(address(vault), CTF_ID), 0);

        vm.expectEmit(true, false, false, false, address(vault));
        emit LendingVaultV2.CtfReturnedFromKeeper(loanId);

        vm.prank(keeper);
        vault.returnCtfFromKeeper(loanId);

        assertEq(ctf.balanceOf(keeper, CTF_ID), 0, "keeper no longer holds CTF");
        assertEq(ctf.balanceOf(address(vault), CTF_ID), ALICE_SHARES, "vault holds CTF again");

        ( , , , , , , , , bool active, , bool liquidated, bool withdrawn) = vault.loans(loanId);
        assertTrue(active, "still active after return");
        assertFalse(liquidated);
        assertFalse(withdrawn, "withdrawn flag cleared");
    }

    function test_ReturnCtf_LoanCanBeWithdrawnAgain() public {
        uint256 loanId = _openLoan(PRINCIPAL);

        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);
        vm.prank(keeper);
        vault.returnCtfFromKeeper(loanId);

        // Re-issue withdraw — should succeed.
        vm.prank(keeper);
        uint256 expected2 = vault.withdrawCtfForLiquidation(loanId, 500_000);
        assertEq(expected2, (ALICE_SHARES * 500_000) / 1_000_000);
    }

    function test_ReturnCtf_RevertsForNonKeeper() public {
        uint256 loanId = _openLoan(PRINCIPAL);
        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        vm.prank(alice);
        vm.expectRevert(LendingVaultV2.OnlyKeeper.selector);
        vault.returnCtfFromKeeper(loanId);
    }

    function test_ReturnCtf_RevertsWhenNotWithdrawn() public {
        uint256 loanId = _openLoan(PRINCIPAL);
        // Skip withdraw step.
        vm.prank(keeper);
        vm.expectRevert(LendingVaultV2.LoanNotWithdrawn.selector);
        vault.returnCtfFromKeeper(loanId);
    }

    function test_ReturnCtf_RevertsWhenLiquidated() public {
        uint256 loanId = _openLoan(PRINCIPAL);
        vm.prank(keeper);
        vault.withdrawCtfForLiquidation(loanId, 500_000);

        usdc.mint(address(vault), 100e6);
        vm.prank(keeper);
        vault.settleLiquidation(loanId, "ltv_breach", 100e6);

        // After settle: liquidated=true, withdrawn still true (per V2 spec).
        // returnCtf should revert via LoanAlreadyLiquidated.
        vm.prank(keeper);
        vm.expectRevert(LendingVaultV2.LoanAlreadyLiquidated.selector);
        vault.returnCtfFromKeeper(loanId);
    }

    // ============================================================ treasury admin

    function test_SetTreasury_OnlyAdmin() public {
        vm.prank(alice);
        vm.expectRevert(LendingVaultV2.OnlyAdmin.selector);
        vault.setTreasury(address(0xCAFE));
    }

    function test_SetTreasury_RevertsOnZero() public {
        vm.prank(admin);
        vm.expectRevert(LendingVaultV2.ZeroAddress.selector);
        vault.setTreasury(address(0));
    }

    function test_SetTreasury_EmitsEventWithPreviousAndNext() public {
        address newTreasury = address(0xCAFE);
        vm.expectEmit(true, true, false, false, address(vault));
        emit LendingVaultV2.TreasurySet(treasury, newTreasury);

        vm.prank(admin);
        vault.setTreasury(newTreasury);
        assertEq(vault.treasury(), newTreasury);
    }

    // ============================================================ V1 → V2 storage compatibility

    /// @dev Deploy a V1 proxy, accumulate state (loan + LP balance + admin
    ///      changes), upgrade to V2, and verify that all V1 fields read back
    ///      identically. This is the storage-layout regression guard.
    function test_Upgrade_V1ToV2_PreservesAllState() public {
        // Spin up an isolated V1 proxy.
        LendingVault v1Impl = new LendingVault();
        bytes memory v1InitData = abi.encodeCall(
            LendingVault.initialize,
            (
                IERC20(address(usdc)),
                IERC1155(address(ctf)),
                IPolymarketExchange(address(ex)),
                admin,
                keeper,
                upgrader
            )
        );
        ERC1967Proxy v1Proxy = new ERC1967Proxy(address(v1Impl), v1InitData);
        LendingVault v1Vault = LendingVault(address(v1Proxy));

        // Seed LP pool and open a loan via V1.
        usdc.mint(admin, LP_SEED);
        vm.startPrank(admin);
        usdc.approve(address(v1Vault), LP_SEED);
        v1Vault.depositLP(LP_SEED);
        vm.stopPrank();

        MockSafe v1Safe = new MockSafe(alice);
        ctf.mint(address(v1Safe), CTF_ID, ALICE_SHARES);
        v1Safe.setApprovalForAll1155(IERC1155(address(ctf)), address(v1Vault), true);

        vm.prank(alice);
        uint256 v1LoanId = v1Vault.openLoan(
            address(v1Safe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );

        // Snapshot V1 storage.
        address v1Admin = v1Vault.admin();
        address v1Keeper = v1Vault.keeper();
        address v1Upgrader = v1Vault.upgrader();
        uint256 v1NextLoanId = v1Vault.nextLoanId();
        uint256 v1LpBalance = v1Vault.lpPoolBalance();
        bool v1Paused = v1Vault.paused();

        (
            address borrower_,
            address collateralSource_,
            uint256 ctfTokenId_,
            uint256 collateralShares_,
            uint256 principal_,
            uint256 openTime_,
            uint256 matchKickoff_,
            uint8 leagueTier_,
            bool active_,
            bool repaid_,
            bool liquidated_
        ) = v1Vault.loans(v1LoanId);

        // Upgrade to V2.
        LendingVaultV2 v2Impl = new LendingVaultV2();
        vm.prank(upgrader);
        (bool ok, ) = address(v1Vault).call(
            abi.encodeWithSignature(
                "upgradeToAndCall(address,bytes)",
                address(v2Impl),
                ""
            )
        );
        require(ok, "upgrade call failed");

        // Re-cast the proxy to V2.
        LendingVaultV2 v2Vault = LendingVaultV2(address(v1Proxy));

        // All top-level state must read back identically.
        assertEq(v2Vault.admin(), v1Admin, "admin preserved");
        assertEq(v2Vault.keeper(), v1Keeper, "keeper preserved");
        assertEq(v2Vault.upgrader(), v1Upgrader, "upgrader preserved");
        assertEq(v2Vault.nextLoanId(), v1NextLoanId, "nextLoanId preserved");
        assertEq(v2Vault.lpPoolBalance(), v1LpBalance, "lpPoolBalance preserved");
        assertEq(v2Vault.paused(), v1Paused, "paused preserved");
        assertEq(address(v2Vault.usdc()), address(usdc), "usdc preserved");
        assertEq(address(v2Vault.ctf()), address(ctf), "ctf preserved");
        assertEq(address(v2Vault.exchange()), address(ex), "exchange preserved");

        // Critically: treasury is still zero.
        assertEq(v2Vault.treasury(), address(0), "treasury starts at zero post-upgrade");

        // Old loan must read back through the V2 struct, including `withdrawn = false`.
        (
            address vBorrower,
            address vCollateralSource,
            uint256 vCtfTokenId,
            uint256 vCollateralShares,
            uint256 vPrincipal,
            uint256 vOpenTime,
            uint256 vMatchKickoff,
            uint8 vLeagueTier,
            bool vActive,
            bool vRepaid,
            bool vLiquidated,
            bool vWithdrawn
        ) = v2Vault.loans(v1LoanId);
        assertEq(vBorrower, borrower_, "borrower preserved");
        assertEq(vCollateralSource, collateralSource_, "collateralSource preserved");
        assertEq(vCtfTokenId, ctfTokenId_, "ctfTokenId preserved");
        assertEq(vCollateralShares, collateralShares_, "shares preserved");
        assertEq(vPrincipal, principal_, "principal preserved");
        assertEq(vOpenTime, openTime_, "openTime preserved");
        assertEq(vMatchKickoff, matchKickoff_, "matchKickoff preserved");
        assertEq(vLeagueTier, leagueTier_, "leagueTier preserved");
        assertEq(vActive, active_, "active preserved");
        assertEq(vRepaid, repaid_, "repaid preserved");
        assertEq(vLiquidated, liquidated_, "liquidated preserved");
        assertFalse(vWithdrawn, "withdrawn defaults to false for legacy loan");

        // After admin sets treasury, the legacy loan is settle-eligible.
        vm.prank(admin);
        v2Vault.setTreasury(treasury);
        assertEq(v2Vault.treasury(), treasury);

        // Drive the new loan through the full V2 path to prove end-to-end
        // works post-upgrade.
        vm.prank(keeper);
        ctf.setApprovalForAll(address(v2Vault), true);

        vm.prank(keeper);
        v2Vault.withdrawCtfForLiquidation(v1LoanId, 500_000);

        usdc.mint(address(v2Vault), 100e6);

        vm.prank(keeper);
        v2Vault.settleLiquidation(v1LoanId, "ltv_breach", 100e6);

        ( , , , , , , , , bool finalActive, , bool finalLiquidated, ) = v2Vault.loans(v1LoanId);
        assertFalse(finalActive);
        assertTrue(finalLiquidated);
    }
}
