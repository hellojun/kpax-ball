// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

/// @notice V3 → V4 upgrade. V4 introduces no new storage slots and reuses
///         every field set by `initializeV3` (USDC.e / pUSD / onramp / offramp
///         max-allowances), so the upgrade is just a plain `upgradeToAndCall`
///         with empty initdata.
///
///         The semantic change in V4: `openLoan` transfers USDC.e directly
///         to the borrower EOA instead of wrapping into pUSD on a PM V2
///         DepositWallet. This works around Polymarket's migration to
///         EIP-7702 delegated EOAs, which broke V3's deterministic deposit
///         wallet derivation.
///
///         Two entry points:
///           - UpgradeV4Direct  : admin EOA == vault.upgrader(). One tx.
///           - UpgradeV4Schedule / UpgradeV4Execute : timelock pair.

contract UpgradeV4Direct is Script {
    function run(address proxy, address v4Impl) external {
        require(proxy != address(0), "proxy zero");
        require(v4Impl != address(0), "v4Impl zero");

        console2.log("== KPAX V3 -> V4 direct upgrade (no timelock) ==");
        console2.log("proxy   :", proxy);
        console2.log("v4Impl  :", v4Impl);

        vm.startBroadcast();
        (bool ok, bytes memory ret) = proxy.call(
            abi.encodeWithSignature("upgradeToAndCall(address,bytes)", v4Impl, "")
        );
        if (!ok) {
            assembly {
                revert(add(ret, 32), mload(ret))
            }
        }
        vm.stopBroadcast();

        console2.log("Upgrade applied. Verify with:");
        console2.log("  cast call <proxy> 'usdc()(address)'   -> should still be USDC.e");
        console2.log("  cast call <proxy> 'pUSD()(address)'   -> should still be pUSD (kept for settle)");
    }
}

contract UpgradeV4Schedule is Script {
    bytes32 public constant SALT = keccak256("KPAX_V4_UPGRADE");

    function run(address timelockAddr, address proxy, address v4Impl) external {
        TimelockController tl = TimelockController(payable(timelockAddr));
        uint256 delay = tl.getMinDelay();

        bytes memory data = abi.encodeWithSignature("upgradeToAndCall(address,bytes)", v4Impl, "");
        bytes32 opId = tl.hashOperation(proxy, 0, data, bytes32(0), SALT);

        console2.log("== KPAX V3 -> V4 schedule (via timelock) ==");
        console2.log("timelock :", timelockAddr);
        console2.log("proxy    :", proxy);
        console2.log("v4Impl   :", v4Impl);
        console2.log("delay (s):", delay);
        console2.log("opId     :"); console2.logBytes32(opId);

        vm.startBroadcast();
        tl.schedule(proxy, 0, data, bytes32(0), SALT, delay);
        vm.stopBroadcast();
    }
}

contract UpgradeV4Execute is Script {
    bytes32 public constant SALT = keccak256("KPAX_V4_UPGRADE");

    function run(address timelockAddr, address proxy, address v4Impl) external {
        TimelockController tl = TimelockController(payable(timelockAddr));
        bytes memory data = abi.encodeWithSignature("upgradeToAndCall(address,bytes)", v4Impl, "");

        console2.log("== KPAX V3 -> V4 execute ==");
        console2.log("proxy   :", proxy);
        console2.log("v4Impl  :", v4Impl);

        vm.startBroadcast();
        tl.execute(proxy, 0, data, bytes32(0), SALT);
        vm.stopBroadcast();
    }
}
