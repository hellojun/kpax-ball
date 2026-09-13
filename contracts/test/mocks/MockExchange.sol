// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IPolymarketExchange} from "../../src/interfaces/IPolymarketExchange.sol";

/// @notice Skeleton; real exchange integration arrives in Sprint 3.
contract MockExchange is IPolymarketExchange {
    function sell(uint256, uint256) external pure returns (uint256) {
        revert("MockExchange: not implemented");
    }
}
