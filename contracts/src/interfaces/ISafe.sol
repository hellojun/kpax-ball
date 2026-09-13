// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal Gnosis Safe interface — only the views KPAX needs to verify
///         that `msg.sender` is an owner of a given Safe (which is what
///         Polymarket's external-wallet users have as their CTF custody proxy).
///
/// Source: Safe v1.3.0 OwnerManager:
///   https://github.com/safe-global/safe-contracts/blob/v1.3.0/contracts/base/OwnerManager.sol
interface ISafe {
    /// @return owners The list of Safe owners (address[]). For Polymarket-Safe
    ///         users this returns a single-element array containing the EOA.
    function getOwners() external view returns (address[] memory owners);

    /// @return True iff `owner` is currently an owner of this Safe.
    function isOwner(address owner) external view returns (bool);

    /// @return The Safe contract version as a semver string (e.g. "1.3.0").
    ///         Used as a sanity check before treating an address as a Safe.
    function VERSION() external view returns (string memory);
}
