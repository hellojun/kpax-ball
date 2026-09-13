// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

import {LendingVaultV2} from "../src/LendingVaultV2.sol";

/// @notice Execute the V2 upgrade batch scheduled by ScheduleV2Upgrade.s.sol.
///         Must be run AT LEAST `timelock.getMinDelay()` seconds after the
///         schedule transaction landed (typically 24h).
///
///         The salt is hardcoded to keccak256("KPAX_V2_UPGRADE_BATCH"), same
///         as the schedule script.
///
/// @dev    Caller must hold EXECUTOR_ROLE on the timelock. Same EOA as the
///         scheduler under the V1 deploy layout.
///
/// Usage:
///   forge script script/ExecuteV2Upgrade.s.sol:ExecuteV2Upgrade \
///     --sig "run(address,address,address,address)" \
///     <timelock> <proxy> <v2Impl> <newUpgrader> \
///     --rpc-url https://polygon-bor-rpc.publicnode.com \
///     --account deployer --sender 0xYourEOA \
///     --broadcast
contract ExecuteV2Upgrade is Script {
    function run(
        address timelockAddr,
        address proxy,
        address v2Impl,
        address newUpgrader
    ) external {
        TimelockController tl = TimelockController(payable(timelockAddr));

        address[] memory targets = new address[](2);
        uint256[] memory values = new uint256[](2);
        bytes[] memory payloads = new bytes[](2);

        // Order MUST match ScheduleV2Upgrade.s.sol: upgrade first, setUpgrader
        // last. msg.sender stays the timelock for the whole batch — if the
        // role were transferred first, the upgrade call would revert with
        // OnlyUpgrader().
        targets[0] = proxy;
        values[0] = 0;
        payloads[0] = abi.encodeWithSignature(
            "upgradeToAndCall(address,bytes)",
            v2Impl,
            ""
        );

        targets[1] = proxy;
        values[1] = 0;
        payloads[1] = abi.encodeWithSelector(
            LendingVaultV2.setUpgrader.selector,
            newUpgrader
        );

        bytes32 predecessor = bytes32(0);
        bytes32 salt = keccak256("KPAX_V2_UPGRADE_BATCH");

        console2.log("== KPAX LendingVault V1 -> V2 upgrade execute ==");
        console2.log("timelock     :", timelockAddr);
        console2.log("proxy        :", proxy);
        console2.log("v2Impl       :", v2Impl);
        console2.log("newUpgrader  :", newUpgrader);

        vm.startBroadcast();
        tl.executeBatch(targets, values, payloads, predecessor, salt);
        vm.stopBroadcast();

        console2.log("");
        console2.log("Upgrade executed. Verify on-chain state:");
        console2.log("  vault.upgrader() should now equal newUpgrader.");
        console2.log(unicode"  vault.treasury() should still be address(0) — admin must call setTreasury manually.");
        console2.log("");
        console2.log("Recommended next admin actions:");
        console2.log("  1. vault.setTreasury(0xcb7A9e4F9366de25F36195Ce96625bE3dEe05bb7)");
        console2.log("  2. keeper EOA: ctf.setApprovalForAll(vault, true)  // for returnCtfFromKeeper");
    }
}
