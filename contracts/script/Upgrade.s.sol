// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

import {LendingVault} from "../src/LendingVault.sol";

/// @notice Two-step UUPS upgrade flow via the TimelockController.
///
/// Step 1 (schedule):
///   forge script script/Upgrade.s.sol:UpgradeSchedule \
///     --sig "run(address,address,address)" \
///     <timelock> <proxy> <newImpl> \
///     --rpc-url $RPC --account multisig --broadcast
///
/// Step 2 (after `delay` seconds elapsed, execute):
///   forge script script/Upgrade.s.sol:UpgradeExecute \
///     --sig "run(address,address,address)" \
///     <timelock> <proxy> <newImpl> \
///     --rpc-url $RPC --account multisig --broadcast
///
/// `multisig` here is the EOA / Safe holding PROPOSER_ROLE on the timelock.
/// For a 2-of-3 Safe, you'd typically build the calldata locally and submit
/// it via the Safe UI rather than via `forge script` — these scripts are
/// here primarily for local anvil testing.

contract UpgradeSchedule is Script {
    function run(address timelockAddr, address proxy, address newImpl) external {
        TimelockController tl = TimelockController(payable(timelockAddr));
        uint256 delay = tl.getMinDelay();

        bytes memory data = abi.encodeWithSignature(
            "upgradeToAndCall(address,bytes)",
            newImpl,
            ""
        );

        console2.log("Scheduling upgrade:");
        console2.log("  proxy   :", proxy);
        console2.log("  newImpl :", newImpl);
        console2.log("  delay   :", delay);

        vm.startBroadcast();
        tl.schedule(proxy, 0, data, bytes32(0), bytes32(0), delay);
        vm.stopBroadcast();

        console2.log("Scheduled. Wait", delay, "seconds before executing.");
    }
}

contract UpgradeExecute is Script {
    function run(address timelockAddr, address proxy, address newImpl) external {
        TimelockController tl = TimelockController(payable(timelockAddr));

        bytes memory data = abi.encodeWithSignature(
            "upgradeToAndCall(address,bytes)",
            newImpl,
            ""
        );

        console2.log("Executing upgrade:");
        console2.log("  proxy   :", proxy);
        console2.log("  newImpl :", newImpl);

        vm.startBroadcast();
        tl.execute(proxy, 0, data, bytes32(0), bytes32(0));
        vm.stopBroadcast();

        console2.log("Upgrade executed.");
    }
}
