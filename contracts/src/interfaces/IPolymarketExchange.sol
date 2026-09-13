// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice 最小化的 Polymarket Exchange 接口。Sprint 1 只定义 sell，
///         真实接口见 Polymarket 的 CTFExchange（Sprint 3 接入时再对齐）。
interface IPolymarketExchange {
    /// @dev Sell `shares` of ERC-1155 token `tokenId` for USDC.
    ///      Caller must have approved the exchange (via setApprovalForAll on CTF).
    /// @return usdcOut The USDC amount received.
    function sell(uint256 tokenId, uint256 shares) external returns (uint256 usdcOut);
}
