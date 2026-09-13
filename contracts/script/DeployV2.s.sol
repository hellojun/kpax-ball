// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";

import {LendingVaultV2} from "../src/LendingVaultV2.sol";

/// @notice Deploy a fresh V2 implementation contract. Does NOT touch the
///         existing UUPS proxy at `0x40892387747Da2f46fecaC902274081F676F1592`.
///
///         The proxy is upgraded separately via the timelock — see
///         `ScheduleV2Upgrade.s.sol` and `ExecuteV2Upgrade.s.sol`.
///
/// @dev    The implementation's constructor calls `_disableInitializers()`,
///         so the bytecode at this address can never be initialized
///         standalone. The only way to use it is via `upgradeToAndCall` from
///         the proxy.
///
/// Usage:
///   cd contracts
///   forge script script/DeployV2.s.sol:DeployV2 \
///     --rpc-url https://polygon-bor-rpc.publicnode.com \
///     --account deployer --sender 0xYourEOA \
///     --broadcast --verify --etherscan-api-key $POLYGONSCAN_KEY
contract DeployV2 is Script {
    function run() external returns (LendingVaultV2 impl) {
        console2.log("== KPAX LendingVaultV2 implementation deploy ==");

        vm.startBroadcast();
        impl = new LendingVaultV2();
        vm.stopBroadcast();

        console2.log("V2 implementation deployed at:", address(impl));
        console2.log("");
        console2.log("Next steps:");
        console2.log("  1. Verify on polygonscan if not auto-verified");
        console2.log("  2. Run ScheduleV2Upgrade.s.sol with this address");
    }
}
