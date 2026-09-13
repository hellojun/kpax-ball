// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";

import {LendingVaultV3} from "../src/LendingVaultV3.sol";

/// @notice Deploy a fresh V3 implementation contract. Does NOT touch the
///         existing UUPS proxy at `0x0f738680bb060ffb01ebabb7415ce8084410ef26`.
///
///         The proxy is upgraded separately via `UpgradeV3.s.sol`, which
///         bundles `upgradeToAndCall(impl, initializeV3(...))` into a single
///         atomic step (so the proxy can never sit in a "V3 impl loaded but
///         unwired" state where USDC.e / pUSD / ramps are unset).
///
/// @dev    The implementation's constructor calls `_disableInitializers()`,
///         so the bytecode at this address can never be initialized
///         standalone. The only way to use it is via `upgradeToAndCall` from
///         the proxy.
///
/// Usage:
///   cd contracts
///   forge script script/DeployV3Impl.s.sol:DeployV3Impl \
///     --rpc-url https://polygon-bor-rpc.publicnode.com \
///     --account deployer --sender 0xYourEOA \
///     --broadcast --verify --etherscan-api-key $POLYGONSCAN_KEY
contract DeployV3Impl is Script {
    function run() external returns (LendingVaultV3 impl) {
        console2.log("== KPAX LendingVaultV3 implementation deploy ==");

        vm.startBroadcast();
        impl = new LendingVaultV3();
        vm.stopBroadcast();

        console2.log("V3 implementation deployed at:", address(impl));
        console2.log("");
        console2.log("Next steps (in order):");
        console2.log("  1. Verify on polygonscan if not auto-verified");
        console2.log("  2. (pre-upgrade) Run CleanupPreV3 to clean V2 state");
        console2.log("  3. Run UpgradeV3 with this impl address");
    }
}
