// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

import {LendingVaultV3} from "../src/LendingVaultV3.sol";

/// @notice Atomic V2 → V3 upgrade. Encodes `initializeV3(...)` as the
///         `upgradeToAndCall` initdata so the proxy switches impls AND wires
///         pUSD / Onramp / Offramp + USDC.e in a single tx. Cannot leave the
///         proxy in a "V3 impl, V3 storage zero" intermediate state.
///
///         Three entry points:
///           - UpgradeV3Direct  : admin EOA == vault.upgrader().
///                                One tx, no timelock dance.
///           - UpgradeV3Schedule: timelock == vault.upgrader().
///                                Schedules the upgrade for `delay` later.
///           - UpgradeV3Execute : runs after the timelock delay elapses.
///
///         Polygon-mainnet defaults are baked in for pUSD / Onramp / Offramp /
///         USDC.e per Phase 1 §3.1 verification (Onramp accepts ONLY USDC.e).

// ---------------------------------------------------------------- shared constants

/// Polygon mainnet addresses (verified against PM CLOB V2 cutover 2026-04-28).
abstract contract _V3Const {
    address constant POLYGON_USDCE   = 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174;
    address constant POLYGON_PUSD    = 0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB;
    address constant POLYGON_ONRAMP  = 0x93070a847efEf7F70739046A929D47a521F5B8ee;
    address constant POLYGON_OFFRAMP = 0x2957922Eb93258b93368531d39fAcCA3B4dC5854;

    /// Returns the abi-encoded calldata for `LendingVaultV3.initializeV3(...)`.
    function _initV3Calldata() internal pure returns (bytes memory) {
        return
            abi.encodeCall(
                LendingVaultV3.initializeV3,
                (
                    IERC20(POLYGON_USDCE),
                    POLYGON_PUSD,
                    POLYGON_ONRAMP,
                    POLYGON_OFFRAMP
                )
            );
    }

    function _logTargets() internal pure {
        console2.log("V3 wiring (post-init):");
        console2.log("  usdc -> USDC.e:", POLYGON_USDCE);
        console2.log("  pUSD          :", POLYGON_PUSD);
        console2.log("  onramp        :", POLYGON_ONRAMP);
        console2.log("  offramp       :", POLYGON_OFFRAMP);
    }
}

// ---------------------------------------------------------------- direct (no timelock)

/// @notice Single-tx upgrade for dev environments where the admin EOA holds
///         upgrader role (DeployV2Direct path). Calls
///         `vault.upgradeToAndCall(v3Impl, initializeV3 calldata)` directly.
///
/// Usage:
///   forge script script/UpgradeV3.s.sol:UpgradeV3Direct \
///     --sig "run(address,address)" <proxy> <v3Impl> \
///     --rpc-url $RPC --account deployer --broadcast
contract UpgradeV3Direct is Script, _V3Const {
    function run(address proxy, address v3Impl) external {
        require(proxy != address(0), "proxy zero");
        require(v3Impl != address(0), "v3Impl zero");

        bytes memory initCall = _initV3Calldata();

        console2.log("== KPAX V2 -> V3 direct upgrade (no timelock) ==");
        console2.log("proxy   :", proxy);
        console2.log("v3Impl  :", v3Impl);
        _logTargets();

        vm.startBroadcast();
        (bool ok, bytes memory ret) = proxy.call(
            abi.encodeWithSignature(
                "upgradeToAndCall(address,bytes)",
                v3Impl,
                initCall
            )
        );
        if (!ok) {
            // Bubble up the revert reason so the operator knows whether the
            // upgrader role check failed, the init reverted, etc.
            assembly {
                revert(add(ret, 32), mload(ret))
            }
        }
        vm.stopBroadcast();

        console2.log("Upgrade applied. Verify with:");
        console2.log("  cast call <proxy> 'usdc()(address)'   ->", POLYGON_USDCE);
        console2.log("  cast call <proxy> 'pUSD()(address)'   ->", POLYGON_PUSD);
        console2.log("  cast call <proxy> 'onramp()(address)' ->", POLYGON_ONRAMP);
    }
}

// ---------------------------------------------------------------- timelock schedule

/// @notice Schedule V3 upgrade through TimelockController. After the timelock
///         delay elapses, run `UpgradeV3Execute` with identical args.
///
///         If your timelock has minDelay > 0 and you want this to land
///         immediately for dev, first run a separate timelock op to
///         `updateDelay(0)`.
///
/// Usage:
///   forge script script/UpgradeV3.s.sol:UpgradeV3Schedule \
///     --sig "run(address,address,address)" \
///     <timelock> <proxy> <v3Impl> \
///     --rpc-url $RPC --account multisig --broadcast
contract UpgradeV3Schedule is Script, _V3Const {
    bytes32 public constant SALT = keccak256("KPAX_V3_UPGRADE_BATCH");

    function run(address timelockAddr, address proxy, address v3Impl) external {
        require(timelockAddr != address(0), "timelock zero");
        require(proxy != address(0), "proxy zero");
        require(v3Impl != address(0), "v3Impl zero");

        TimelockController tl = TimelockController(payable(timelockAddr));
        uint256 delay = tl.getMinDelay();

        bytes memory data = abi.encodeWithSignature(
            "upgradeToAndCall(address,bytes)",
            v3Impl,
            _initV3Calldata()
        );

        bytes32 opId = tl.hashOperation(proxy, 0, data, bytes32(0), SALT);

        console2.log("== KPAX V2 -> V3 schedule (via timelock) ==");
        console2.log("timelock :", timelockAddr);
        console2.log("proxy    :", proxy);
        console2.log("v3Impl   :", v3Impl);
        console2.log("delay (s):", delay);
        console2.log("salt     :"); console2.logBytes32(SALT);
        console2.log("opId     :"); console2.logBytes32(opId);
        _logTargets();

        vm.startBroadcast();
        tl.schedule(proxy, 0, data, bytes32(0), SALT, delay);
        vm.stopBroadcast();

        console2.log("");
        console2.log("Scheduled. Wait", delay, "seconds, then run UpgradeV3Execute.");
    }
}

// ---------------------------------------------------------------- timelock execute

/// @notice Execute the V3 upgrade scheduled by UpgradeV3Schedule. Must be run
///         AFTER the timelock delay has elapsed.
///
/// Usage:
///   forge script script/UpgradeV3.s.sol:UpgradeV3Execute \
///     --sig "run(address,address,address)" \
///     <timelock> <proxy> <v3Impl> \
///     --rpc-url $RPC --account multisig --broadcast
contract UpgradeV3Execute is Script, _V3Const {
    bytes32 public constant SALT = keccak256("KPAX_V3_UPGRADE_BATCH");

    function run(address timelockAddr, address proxy, address v3Impl) external {
        TimelockController tl = TimelockController(payable(timelockAddr));

        bytes memory data = abi.encodeWithSignature(
            "upgradeToAndCall(address,bytes)",
            v3Impl,
            _initV3Calldata()
        );

        console2.log("== KPAX V2 -> V3 execute (via timelock) ==");
        console2.log("timelock :", timelockAddr);
        console2.log("proxy    :", proxy);
        console2.log("v3Impl   :", v3Impl);
        _logTargets();

        vm.startBroadcast();
        tl.execute(proxy, 0, data, bytes32(0), SALT);
        vm.stopBroadcast();

        console2.log("Upgrade executed. Verify with:");
        console2.log("  cast call <proxy> 'usdc()(address)'   ->", POLYGON_USDCE);
        console2.log("  cast call <proxy> 'pUSD()(address)'   ->", POLYGON_PUSD);
        console2.log("  cast call <proxy> 'onramp()(address)' ->", POLYGON_ONRAMP);
    }
}
