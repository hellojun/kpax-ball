// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";

import {LendingVault} from "../src/LendingVault.sol";
import {IPolymarketExchange} from "../src/interfaces/IPolymarketExchange.sol";

import {MockUSDC} from "./mocks/MockUSDC.sol";
import {MockCTF} from "./mocks/MockCTF.sol";
import {MockExchange} from "./mocks/MockExchange.sol";
import {MockSafe} from "./mocks/MockSafe.sol";
import {LendingVaultV2Mock} from "./mocks/LendingVaultV2Mock.sol";
import {NonUUPSImplMock} from "./mocks/NonUUPSImplMock.sol";

contract LendingVaultTest is Test {
    MockUSDC usdc;
    MockCTF ctf;
    MockExchange ex;
    LendingVault vault; // proxy, casted to V1 ABI

    // Polymarket-style setup: each user owns a Safe proxy that holds the CTF
    // collateral. The user signs Vault calls via their EOA; the Safe holds the
    // shares and pre-approves the Vault to pull them.
    address admin = address(0xA11CE);
    address keeper = address(0xBEEF);
    address upgrader = address(0xDA0DA0); // 24h timelock in production

    address alice = address(0xA1);
    MockSafe aliceSafe;

    address mallory = address(0xBAD);
    MockSafe mallorySafe;

    uint256 constant CTF_ID = 42;
    uint256 constant ALICE_SHARES = 833;
    uint256 constant PRINCIPAL = 250e6; // 250 USDC
    uint256 constant LP_SEED = 10_000e6;

    function setUp() public {
        usdc = new MockUSDC();
        ctf = new MockCTF();
        ex = new MockExchange();

        // Deploy implementation + proxy.
        LendingVault impl = new LendingVault();
        bytes memory initData = abi.encodeCall(
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
        ERC1967Proxy proxy = new ERC1967Proxy(address(impl), initData);
        vault = LendingVault(address(proxy));

        // LP pool seeded by admin.
        usdc.mint(admin, LP_SEED);
        vm.startPrank(admin);
        usdc.approve(address(vault), LP_SEED);
        vault.depositLP(LP_SEED);
        vm.stopPrank();

        // Alice's Polymarket Safe holds her CTF collateral.
        aliceSafe = new MockSafe(alice);
        ctf.mint(address(aliceSafe), CTF_ID, ALICE_SHARES);
        // Approval is granted by the Safe itself (mimics
        // `Safe.execTransaction(setApprovalForAll(vault, true))`).
        aliceSafe.setApprovalForAll1155(IERC1155(address(ctf)), address(vault), true);

        // Mallory has her own Safe but with NO collateral; she'll try to
        // borrow against Alice's Safe.
        mallorySafe = new MockSafe(mallory);
    }

    // -------------------- initializer --------------------

    function test_Initialize_RevertsOnSecondCall() public {
        vm.expectRevert(); // InvalidInitialization
        vault.initialize(
            IERC20(address(usdc)),
            IERC1155(address(ctf)),
            IPolymarketExchange(address(ex)),
            admin,
            keeper,
            upgrader
        );
    }

    function test_Initialize_RevertsOnImplementation() public {
        // Direct call on the implementation contract should revert because
        // the constructor disabled initializers.
        LendingVault impl = new LendingVault();
        vm.expectRevert();
        impl.initialize(
            IERC20(address(usdc)),
            IERC1155(address(ctf)),
            IPolymarketExchange(address(ex)),
            admin,
            keeper,
            upgrader
        );
    }

    // -------------------- openLoan happy path --------------------

    function test_OpenLoan_Success() public {
        vm.prank(alice);
        uint256 loanId = vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );

        assertEq(loanId, 0, "loanId");
        assertEq(usdc.balanceOf(alice), PRINCIPAL, "alice EOA got USDC");
        assertEq(usdc.balanceOf(address(aliceSafe)), 0, "Safe should NOT receive USDC");
        assertEq(ctf.balanceOf(address(vault), CTF_ID), ALICE_SHARES, "vault holds CTF");
        assertEq(ctf.balanceOf(address(aliceSafe), CTF_ID), 0, "Safe no longer holds CTF");
        assertEq(vault.lpPoolBalance(), LP_SEED - PRINCIPAL, "pool debited");
    }

    // -------------------- ownership protection --------------------

    function test_OpenLoan_RevertsWhenAttackerUsesAnotherSafe() public {
        vm.prank(mallory);
        vm.expectRevert(LendingVault.NotProxyOwner.selector);
        vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );
    }

    function test_OpenLoan_RevertsWhenCollateralSourceIsEoa() public {
        vm.prank(alice);
        vm.expectRevert(LendingVault.NotProxyOwner.selector);
        vault.openLoan(
            alice,
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );
    }

    function test_OpenLoan_RevertsOnZeroCollateralSource() public {
        vm.prank(alice);
        vm.expectRevert(LendingVault.CollateralSourceZero.selector);
        vault.openLoan(
            address(0),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );
    }

    // -------------------- input validation --------------------

    function test_OpenLoan_RevertsOnInsufficientLiquidity() public {
        vm.prank(alice);
        vm.expectRevert(LendingVault.InsufficientLiquidity.selector);
        vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            LP_SEED + 1,
            block.timestamp + 7 days,
            1
        );
    }

    function test_OpenLoan_RevertsOnInvalidLeagueTier() public {
        vm.prank(alice);
        vm.expectRevert(LendingVault.InvalidLeagueTier.selector);
        vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            0
        );
    }

    function test_OpenLoan_RevertsWhenPaused() public {
        vm.prank(admin);
        vault.setPaused(true);

        vm.prank(alice);
        vm.expectRevert("paused");
        vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );
    }

    // -------------------- debtOf --------------------

    function test_DebtOf_AccruesLinearly() public {
        vm.prank(alice);
        uint256 loanId = vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );

        (uint256 total0, uint256 int0) = vault.debtOf(loanId);
        assertEq(total0, PRINCIPAL);
        assertEq(int0, 0);

        vm.warp(block.timestamp + 30 days);
        (uint256 total30, uint256 int30) = vault.debtOf(loanId);
        uint256 expected = (PRINCIPAL * 1200 * 30 days) / (10_000 * 31_536_000);
        assertEq(int30, expected, "interest exact formula");
        assertEq(total30, PRINCIPAL + expected);
    }

    // -------------------- repay --------------------

    function test_Repay_Full_ReturnsCollateralToSafe() public {
        vm.prank(alice);
        uint256 loanId = vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );

        vm.warp(block.timestamp + 7 days);
        (uint256 totalDebt,) = vault.debtOf(loanId);

        usdc.mint(alice, totalDebt - PRINCIPAL);
        vm.startPrank(alice);
        usdc.approve(address(vault), totalDebt);
        vault.repay(loanId);
        vm.stopPrank();

        assertEq(ctf.balanceOf(address(aliceSafe), CTF_ID), ALICE_SHARES, "Safe got CTF back");
        assertEq(ctf.balanceOf(alice, CTF_ID), 0, "EOA does NOT receive CTF");
        assertEq(ctf.balanceOf(address(vault), CTF_ID), 0, "vault released CTF");
        assertGt(vault.lpPoolBalance(), LP_SEED, "interest stays in LP pool");
    }

    function test_Repay_RevertsForNonBorrower() public {
        vm.prank(alice);
        uint256 loanId = vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );

        vm.prank(mallory);
        vm.expectRevert(LendingVault.NotBorrower.selector);
        vault.repay(loanId);
    }

    // -------------------- admin gating --------------------

    function test_SetKeeper_OnlyAdmin() public {
        vm.prank(alice);
        vm.expectRevert(LendingVault.OnlyAdmin.selector);
        vault.setKeeper(address(0xFEED));
    }

    function test_SetKeeper_Updates() public {
        address newKeeper = address(0xFEED);
        vm.prank(admin);
        vault.setKeeper(newKeeper);
        assertEq(vault.keeper(), newKeeper);
    }

    function test_SetAdmin_Updates() public {
        address newAdmin = address(0xC0DE);
        vm.prank(admin);
        vault.setAdmin(newAdmin);
        assertEq(vault.admin(), newAdmin);
    }

    // -------------------- liquidate (V1 atomic stub) --------------------
    // Removed in Sprint 4 V2: the V1 atomic `liquidate(loanId, reason, priceE6)`
    // function is being deleted in favour of the 4-step V2 flow
    // (withdrawCtfForLiquidation → off-chain sell → settleLiquidation /
    //  returnCtfFromKeeper). The corresponding test cases now live in
    // `LendingVaultV2.t.sol`. The V1 implementation contract on-chain still
    // exposes `liquidate` for backwards compat, but we don't exercise it
    // here because (a) it's slated for retirement once the proxy points at
    // V2, and (b) the new LoanLiquidated event signature differs from the
    // V1 one this test file would rely on.

    // -------------------- emergency hatches --------------------

    function test_EmergencyWithdrawERC20_RequiresPause() public {
        vm.prank(admin);
        vm.expectRevert(LendingVault.NotPaused.selector);
        vault.emergencyWithdrawERC20(IERC20(address(usdc)), admin, 1);
    }

    function test_EmergencyWithdrawERC20_OnlyAdmin() public {
        vm.prank(admin);
        vault.setPaused(true);

        vm.prank(alice);
        vm.expectRevert(LendingVault.OnlyAdmin.selector);
        vault.emergencyWithdrawERC20(IERC20(address(usdc)), alice, 1);
    }

    function test_EmergencyWithdrawERC20_PullsUsdcAndAdjustsAccounting() public {
        vm.prank(alice);
        vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );
        uint256 vaultBalanceBefore = usdc.balanceOf(address(vault));
        uint256 poolBefore = vault.lpPoolBalance();

        vm.startPrank(admin);
        vault.setPaused(true);
        vault.emergencyWithdrawERC20(IERC20(address(usdc)), admin, vaultBalanceBefore);
        vm.stopPrank();

        assertEq(usdc.balanceOf(address(vault)), 0, "vault drained");
        assertEq(usdc.balanceOf(admin), vaultBalanceBefore, "admin holds funds");
        assertEq(vaultBalanceBefore, poolBefore);
        assertEq(vault.lpPoolBalance(), 0, "pool zeroed");
    }

    function test_EmergencyReturnCollateral_RequiresPause() public {
        vm.prank(alice);
        uint256 loanId = vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );

        vm.prank(admin);
        vm.expectRevert(LendingVault.NotPaused.selector);
        vault.emergencyReturnCollateral(loanId);
    }

    function test_EmergencyReturnCollateral_SendsBackToProxyOnly() public {
        vm.prank(alice);
        uint256 loanId = vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );
        assertEq(ctf.balanceOf(address(vault), CTF_ID), ALICE_SHARES);
        assertEq(ctf.balanceOf(address(aliceSafe), CTF_ID), 0);

        vm.startPrank(admin);
        vault.setPaused(true);
        vault.emergencyReturnCollateral(loanId);
        vm.stopPrank();

        assertEq(ctf.balanceOf(address(aliceSafe), CTF_ID), ALICE_SHARES, "Safe got CTF");
        assertEq(ctf.balanceOf(admin, CTF_ID), 0, "admin can't keep CTF");
        assertEq(ctf.balanceOf(address(vault), CTF_ID), 0);

        ( , , , , , , , , bool active, , ) = vault.loans(loanId);
        assertEq(active, false, "loan inactive");
    }

    // -------------------- UUPS upgrade --------------------

    function test_Upgrade_AuthorizedSucceedsAndStateSurvives() public {
        // Open a loan, accrue some state.
        vm.prank(alice);
        vault.openLoan(
            address(aliceSafe),
            CTF_ID,
            ALICE_SHARES,
            PRINCIPAL,
            block.timestamp + 7 days,
            1
        );
        uint256 lpBefore = vault.lpPoolBalance();
        uint256 nextIdBefore = vault.nextLoanId();

        // Deploy V2 implementation and upgrade through `upgrader`.
        LendingVaultV2Mock v2 = new LendingVaultV2Mock();
        vm.prank(upgrader);
        (bool ok, ) = address(vault).call(
            abi.encodeWithSignature(
                "upgradeToAndCall(address,bytes)",
                address(v2),
                ""
            )
        );
        require(ok, "upgrade call failed");

        // State survives (storage layout preserved).
        assertEq(vault.lpPoolBalance(), lpBefore, "lpPoolBalance preserved");
        assertEq(vault.nextLoanId(), nextIdBefore, "nextLoanId preserved");

        // V2 has a new function — call it through the proxy.
        (bool ok2, bytes memory ret) = address(vault).call(
            abi.encodeWithSignature("v2Marker()")
        );
        require(ok2, "v2Marker call failed");
        uint256 marker = abi.decode(ret, (uint256));
        assertEq(marker, 4242, "V2 reachable via proxy");
    }

    function test_Upgrade_RevertsForUnauthorized() public {
        LendingVaultV2Mock v2 = new LendingVaultV2Mock();

        // Admin cannot upgrade — only `upgrader` (timelock) can.
        vm.prank(admin);
        (bool ok, ) = address(vault).call(
            abi.encodeWithSignature(
                "upgradeToAndCall(address,bytes)",
                address(v2),
                ""
            )
        );
        assertFalse(ok, "admin should not be able to upgrade");

        // Random EOA can't either.
        vm.prank(alice);
        (bool ok2, ) = address(vault).call(
            abi.encodeWithSignature(
                "upgradeToAndCall(address,bytes)",
                address(v2),
                ""
            )
        );
        assertFalse(ok2, "alice should not be able to upgrade");
    }

    function test_Upgrade_RevertsForNonUUPSImplementation() public {
        // A new implementation that isn't UUPS-compatible should be rejected.
        NonUUPSImplMock bad = new NonUUPSImplMock();

        vm.prank(upgrader);
        (bool ok, ) = address(vault).call(
            abi.encodeWithSignature(
                "upgradeToAndCall(address,bytes)",
                address(bad),
                ""
            )
        );
        assertFalse(ok, "non-UUPS impl should be rejected");
    }

    function test_SetUpgrader_OnlyUpgrader() public {
        address newUp = address(0xBABE);

        vm.prank(admin);
        vm.expectRevert(LendingVault.OnlyUpgrader.selector);
        vault.setUpgrader(newUp);

        vm.prank(upgrader);
        vault.setUpgrader(newUp);
        assertEq(vault.upgrader(), newUp);
    }
}
