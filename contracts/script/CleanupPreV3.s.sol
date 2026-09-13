// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {LendingVaultV2} from "../src/LendingVaultV2.sol";

/// @notice Pre-V3 cleanup script. Run this BEFORE `UpgradeV3*`.
///
/// Why: V3 swaps the vault's underlying from Native USDC → USDC.e during
///      `initializeV3`. Any Native USDC sitting in the vault (LP reserves +
///      stale loans' principal) becomes orphaned post-upgrade — it would
///      still be on-chain at the vault address, but `lpPoolBalance` and the
///      withdraw / settle paths would all read it as USDC.e, which it isn't.
///
///      Active V2 loans likewise need to be cleared so the V3 borrowerProxy
///      mapping isn't out of sync with `loans[]`.
///
/// Effects (atomically run by admin EOA):
///   1. setPaused(true)                              — required by emergency hatches
///   2. emergencyReturnCollateral(loanId) for each   — CTF goes back to borrower's
///      legacy proxy. Borrower keeps both CTF and the principal they borrowed —
///      acceptable LP loss in dev (decision: 2026-05-05).
///   3. emergencyWithdrawERC20(USDC, admin, balance) — drains Native USDC out
///   4. setPaused(false)                              — re-open for V3 upgrade
///
/// IMPORTANT: this script does NOT run the V3 upgrade. After it succeeds,
/// run `UpgradeV3Direct` (or the timelock pair) to swap the impl + run
/// `initializeV3`. Then re-fund LP with USDC.e via the standard depositLP path.
///
/// Usage:
///   forge script script/CleanupPreV3.s.sol:CleanupPreV3 \
///     --sig "run(address,uint256[])" \
///     <proxy> "[53]" \
///     --rpc-url $RPC --account admin --broadcast
contract CleanupPreV3 is Script {
    /// Polygon mainnet Native USDC (vault's V2 underlying).
    address constant POLYGON_NATIVE_USDC = 0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359;

    function run(address proxy, uint256[] calldata loansToReturn) external {
        require(proxy != address(0), "proxy zero");
        _logHeader(proxy, loansToReturn.length);
        _execute(proxy, loansToReturn, msg.sender);
        _logFooter();
    }

    function _logHeader(address proxy, uint256 loanCount) internal view {
        console2.log("== KPAX pre-V3 cleanup ==");
        console2.log("proxy             :", proxy);
        console2.log("admin             :", msg.sender);
        console2.log("Native USDC bal   :", IERC20(POLYGON_NATIVE_USDC).balanceOf(proxy));
        console2.log("loans to return   :", loanCount);
    }

    function _execute(address proxy, uint256[] calldata loansToReturn, address admin) internal {
        LendingVaultV2 vault = LendingVaultV2(proxy);
        IERC20 usdc = IERC20(POLYGON_NATIVE_USDC);

        vm.startBroadcast();

        vault.setPaused(true);

        for (uint256 i = 0; i < loansToReturn.length; i++) {
            console2.log("  emergencyReturnCollateral", loansToReturn[i]);
            vault.emergencyReturnCollateral(loansToReturn[i]);
        }

        uint256 sweep = usdc.balanceOf(proxy);
        if (sweep > 0) {
            console2.log("  emergencyWithdrawERC20 USDC", sweep);
            vault.emergencyWithdrawERC20(usdc, admin, sweep);
        }

        vault.setPaused(false);

        vm.stopBroadcast();
    }

    function _logFooter() internal pure {
        console2.log("");
        console2.log("Cleanup complete. Vault state should now be:");
        console2.log("  paused:         false");
        console2.log("  lpPoolBalance:  0");
        console2.log("  Native USDC:    0");
        console2.log("  active loans:   0 (only the cleared ones - verify off-script)");
        console2.log("");
        console2.log("Next: deploy V3 impl + run UpgradeV3Direct.");
    }
}
