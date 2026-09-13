// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC1155Receiver} from "@openzeppelin/contracts/token/ERC1155/IERC1155Receiver.sol";
import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";

import {ISafe} from "../../src/interfaces/ISafe.sol";

/// @notice Minimal Gnosis-Safe-shaped contract for unit tests. Holds a single
///         owner and exposes `isOwner` / `getOwners` per the Safe v1.3 ABI.
///         Provides a convenience `runTx` helper so tests can issue arbitrary
///         calls from the Safe (mimicking what `Safe.execTransaction` would
///         do after on-chain signature verification).
contract MockSafe is ISafe, IERC1155Receiver {
    address public ownerAddr;

    constructor(address _owner) {
        ownerAddr = _owner;
    }

    // ---- ISafe ----
    function isOwner(address candidate) external view returns (bool) {
        return candidate == ownerAddr;
    }

    function getOwners() external view returns (address[] memory) {
        address[] memory arr = new address[](1);
        arr[0] = ownerAddr;
        return arr;
    }

    function VERSION() external pure returns (string memory) {
        return "1.3.0";
    }

    // ---- helpers (test-only; real Safe would gate this on owner signatures) ----
    /// @dev Approve an ERC-1155 operator (mimics `setApprovalForAll` issued
    ///      from inside `Safe.execTransaction`).
    function setApprovalForAll1155(IERC1155 token, address operator, bool approved) external {
        token.setApprovalForAll(operator, approved);
    }

    /// @dev Run an arbitrary call as if it came from the Safe — equivalent to
    ///      `Safe.execTransaction` minus the signature gate.
    function runTx(address to, uint256 value, bytes calldata data) external returns (bytes memory) {
        (bool ok, bytes memory ret) = to.call{value: value}(data);
        require(ok, "MockSafe: call failed");
        return ret;
    }

    // ---- IERC1155Receiver ----
    function onERC1155Received(address, address, uint256, uint256, bytes calldata)
        external
        pure
        returns (bytes4)
    {
        return IERC1155Receiver.onERC1155Received.selector;
    }

    function onERC1155BatchReceived(address, address, uint256[] calldata, uint256[] calldata, bytes calldata)
        external
        pure
        returns (bytes4)
    {
        return IERC1155Receiver.onERC1155BatchReceived.selector;
    }

    function supportsInterface(bytes4 id) external pure returns (bool) {
        return id == type(IERC1155Receiver).interfaceId;
    }
}
