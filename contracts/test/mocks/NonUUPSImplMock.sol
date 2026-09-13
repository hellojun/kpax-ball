// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice An implementation contract that does NOT expose `proxiableUUID`.
///         Used to verify UUPSUpgradeable rejects non-UUPS targets.
contract NonUUPSImplMock {
    uint256 public junk;

    function setJunk(uint256 v) external {
        junk = v;
    }
}
