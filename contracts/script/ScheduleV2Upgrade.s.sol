// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

import {LendingVaultV2} from "../src/LendingVaultV2.sol";

/// @notice Schedule the V1 → V2 upgrade as a single timelock batch.
///         The batch contains TWO calls (atomic in execute, but each
///         scheduled individually because OZ TimelockController has both a
///         single `schedule` and a `scheduleBatch` API; we use `scheduleBatch`
///         for atomicity).
///
///           call 1: vault.setUpgrader(newUpgrader)        // K18: hand upgrader to admin EOA
///           call 2: vault.upgradeToAndCall(v2Impl, "")    // swap implementation
///
///         The order matters semantically (we want admin to be able to do
///         emergency upgrades after this transition completes), but both run
///         atomically inside `executeBatch`, so the on-chain effect is
///         identical regardless of array order.
///
/// @dev    Caller must hold PROPOSER_ROLE on the timelock. Per the V1 deploy
///         (with `MULTISIG_ADDRESS` env unset), the broadcaster EOA was set
///         as proposer/executor, so the admin EOA can call this directly.
///
/// Usage:
///   forge script script/ScheduleV2Upgrade.s.sol:ScheduleV2Upgrade \
///     --sig "run(address,address,address,address)" \
///     <timelock> <proxy> <v2Impl> <newUpgrader> \
///     --rpc-url https://polygon-bor-rpc.publicnode.com \
///     --account deployer --sender 0xYourEOA \
///     --broadcast
///
///   Example values:
///     timelock     : 0x... (from V1 deploy logs)
///     proxy        : 0x40892387747Da2f46fecaC902274081F676F1592
///     v2Impl       : <output of DeployV2.s.sol>
///     newUpgrader  : 0xYourAdminEOA (the address that will hold upgrader role post-batch)
contract ScheduleV2Upgrade is Script {
    function run(
        address timelockAddr,
        address proxy,
        address v2Impl,
        address newUpgrader
    ) external {
        require(timelockAddr != address(0), "timelock zero");
        require(proxy != address(0), "proxy zero");
        require(v2Impl != address(0), "v2Impl zero");
        require(newUpgrader != address(0), "newUpgrader zero");

        TimelockController tl = TimelockController(payable(timelockAddr));
        uint256 delay = tl.getMinDelay();

        // Build the batch.
        address[] memory targets = new address[](2);
        uint256[] memory values = new uint256[](2);
        bytes[] memory payloads = new bytes[](2);

        // Call 1: upgradeToAndCall(v2Impl, "")
        // ORDER MATTERS: msg.sender stays the timelock for the whole batch.
        // If setUpgrader ran first, this onlyUpgrader call would revert
        // because the timelock would no longer hold the role.
        // V2 doesn't need a reinitializer; `treasury` defaults to zero and
        // admin sets it manually post-execute.
        targets[0] = proxy;
        values[0] = 0;
        payloads[0] = abi.encodeWithSignature(
            "upgradeToAndCall(address,bytes)",
            v2Impl,
            ""
        );

        // Call 2: setUpgrader(newUpgrader) — last so the timelock keeps
        // upgrader power until the upgrade itself is committed.
        targets[1] = proxy;
        values[1] = 0;
        payloads[1] = abi.encodeWithSelector(
            LendingVaultV2.setUpgrader.selector,
            newUpgrader
        );

        bytes32 predecessor = bytes32(0);
        bytes32 salt = keccak256("KPAX_V2_UPGRADE_BATCH"); // deterministic, easy to recover for execute

        bytes32 operationId = tl.hashOperationBatch(
            targets,
            values,
            payloads,
            predecessor,
            salt
        );

        console2.log("== KPAX LendingVault V1 -> V2 upgrade scheduling ==");
        console2.log("timelock      :", timelockAddr);
        console2.log("proxy         :", proxy);
        console2.log("v2Impl        :", v2Impl);
        console2.log("newUpgrader   :", newUpgrader);
        console2.log("delay (s)     :", delay);
        console2.logBytes32(salt);
        console2.log("operationId   (record for execute step):");
        console2.logBytes32(operationId);

        vm.startBroadcast();
        tl.scheduleBatch(targets, values, payloads, predecessor, salt, delay);
        vm.stopBroadcast();

        console2.log("");
        console2.log("Scheduled. Wait", delay, "seconds, then run ExecuteV2Upgrade with the same args.");
    }
}
