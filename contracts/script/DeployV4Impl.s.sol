// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";

import {LendingVaultV4} from "../src/LendingVaultV4.sol";

/// @notice Deploy a fresh V4 implementation contract. Does NOT touch the
///         existing UUPS proxy at `0x0f73…ef26`. Upgrade the proxy via
///         `UpgradeV4Direct`.
///
///         V4 reuses V3's storage as-is (no new storage slots, no init call),
///         so upgrading is just `upgradeToAndCall(v4Impl, "")`.
///
/// Usage:
///   forge script script/DeployV4Impl.s.sol:DeployV4Impl \
///     --rpc-url https://polygon-bor-rpc.publicnode.com \
///     --account deployer --sender 0xYourEOA \
///     --broadcast --verify --etherscan-api-key $POLYGONSCAN_KEY
contract DeployV4Impl is Script {
    function run() external returns (LendingVaultV4 impl) {
        console2.log("== KPAX LendingVaultV4 implementation deploy ==");

        vm.startBroadcast();
        impl = new LendingVaultV4();
        vm.stopBroadcast();

        console2.log("V4 implementation deployed at:", address(impl));
        console2.log("");
        console2.log("Next: forge script script/UpgradeV4.s.sol:UpgradeV4Direct \\");
        console2.log("        --sig 'run(address,address)' <proxy> <this>");
    }
}
