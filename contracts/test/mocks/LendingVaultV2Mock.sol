// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {LendingVault} from "../../src/LendingVault.sol";

/// @notice Drop-in V2 used by upgrade tests. Inherits the V1 storage layout
///         exactly (so we don't reorder slots) and only adds a new pure
///         marker function so the test can confirm the upgrade landed.
contract LendingVaultV2Mock is LendingVault {
    function v2Marker() external pure returns (uint256) {
        return 4242;
    }
}
