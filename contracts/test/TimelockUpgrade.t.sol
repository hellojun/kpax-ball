// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

import {LendingVault} from "../src/LendingVault.sol";
import {IPolymarketExchange} from "../src/interfaces/IPolymarketExchange.sol";

import {MockUSDC} from "./mocks/MockUSDC.sol";
import {MockCTF} from "./mocks/MockCTF.sol";
import {MockExchange} from "./mocks/MockExchange.sol";
import {LendingVaultV2Mock} from "./mocks/LendingVaultV2Mock.sol";

/// @notice End-to-end coverage of the timelock-gated upgrade flow.
/// @dev    Uses the same multisig EOA for proposer & executor (matches the
///         Deploy.s.sol production layout).
contract TimelockUpgradeTest is Test {
    MockUSDC usdc;
    MockCTF ctf;
    MockExchange ex;
    LendingVault vault;
    TimelockController timelock;

    address admin = address(0xA11CE);
    address keeper = address(0xBEEF);
    address multisig = address(0xC0DEC0DE);

    uint256 constant DELAY = 24 hours;

    function setUp() public {
        usdc = new MockUSDC();
        ctf = new MockCTF();
        ex = new MockExchange();

        // Spin up timelock with multisig as proposer/executor, no extra admin
        // (self-administered).
        address[] memory proposers = new address[](1);
        proposers[0] = multisig;
        address[] memory executors = new address[](1);
        executors[0] = multisig;
        timelock = new TimelockController(DELAY, proposers, executors, address(0));

        // Deploy implementation + proxy with timelock as upgrader.
        LendingVault impl = new LendingVault();
        bytes memory initData = abi.encodeCall(
            LendingVault.initialize,
            (
                IERC20(address(usdc)),
                IERC1155(address(ctf)),
                IPolymarketExchange(address(ex)),
                admin,
                keeper,
                address(timelock)
            )
        );
        ERC1967Proxy proxy = new ERC1967Proxy(address(impl), initData);
        vault = LendingVault(address(proxy));
    }

    function test_TimelockDelay_BlocksImmediateExecute() public {
        LendingVaultV2Mock v2 = new LendingVaultV2Mock();
        bytes memory upgradeCalldata = abi.encodeWithSignature(
            "upgradeToAndCall(address,bytes)",
            address(v2),
            ""
        );

        vm.startPrank(multisig);
        timelock.schedule(
            address(vault),
            0,
            upgradeCalldata,
            bytes32(0),
            bytes32(0),
            DELAY
        );

        // Try to execute immediately — should revert because operation isn't ready.
        vm.expectRevert();
        timelock.execute(address(vault), 0, upgradeCalldata, bytes32(0), bytes32(0));
        vm.stopPrank();
    }

    function test_TimelockDelay_ExecutesAfterDelay() public {
        LendingVaultV2Mock v2 = new LendingVaultV2Mock();
        bytes memory upgradeCalldata = abi.encodeWithSignature(
            "upgradeToAndCall(address,bytes)",
            address(v2),
            ""
        );

        vm.startPrank(multisig);
        timelock.schedule(
            address(vault),
            0,
            upgradeCalldata,
            bytes32(0),
            bytes32(0),
            DELAY
        );

        vm.warp(block.timestamp + DELAY + 1);

        timelock.execute(address(vault), 0, upgradeCalldata, bytes32(0), bytes32(0));
        vm.stopPrank();

        // V2 marker reachable through the proxy.
        (bool ok, bytes memory ret) = address(vault).call(
            abi.encodeWithSignature("v2Marker()")
        );
        require(ok, "v2Marker not callable");
        uint256 marker = abi.decode(ret, (uint256));
        assertEq(marker, 4242, "upgrade actually landed");
    }

    function test_TimelockDelay_NonProposerCannotSchedule() public {
        LendingVaultV2Mock v2 = new LendingVaultV2Mock();
        bytes memory upgradeCalldata = abi.encodeWithSignature(
            "upgradeToAndCall(address,bytes)",
            address(v2),
            ""
        );

        vm.prank(admin); // not a proposer
        vm.expectRevert();
        timelock.schedule(
            address(vault),
            0,
            upgradeCalldata,
            bytes32(0),
            bytes32(0),
            DELAY
        );
    }

    function test_TimelockDelay_BelowMinDelayReverts() public {
        LendingVaultV2Mock v2 = new LendingVaultV2Mock();
        bytes memory upgradeCalldata = abi.encodeWithSignature(
            "upgradeToAndCall(address,bytes)",
            address(v2),
            ""
        );

        // Trying to schedule with a delay shorter than the minimum should fail.
        vm.prank(multisig);
        vm.expectRevert();
        timelock.schedule(
            address(vault),
            0,
            upgradeCalldata,
            bytes32(0),
            bytes32(0),
            DELAY - 1
        );
    }
}
