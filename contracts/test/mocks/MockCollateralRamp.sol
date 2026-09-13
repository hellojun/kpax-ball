// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @notice Test stand-in for Polymarket V2's `CollateralOnramp`. Real on-chain
///         contract: 0x93070a847efEf7F70739046A929D47a521F5B8ee
///         Mechanics: pulls USDC.e from `msg.sender`, mints 1:1 pUSD into
///         `_to`. We simulate "minting" by transferring from a pre-funded
///         pUSD reserve held by this mock.
contract MockOnramp {
    IERC20 public immutable usdce;
    IMintableERC20 public immutable pusd;

    constructor(address _usdce, address _pusd) {
        usdce = IERC20(_usdce);
        pusd = IMintableERC20(_pusd);
    }

    function wrap(address asset, address to, uint256 amount) external {
        require(asset == address(usdce), "MockOnramp: only usdce");
        require(usdce.transferFrom(msg.sender, address(this), amount), "wrap pull failed");
        pusd.mint(to, amount);
    }
}

/// @notice Test stand-in for Polymarket V2's `CollateralOfframp`. Real on-chain
///         contract: 0x2957922Eb93258b93368531d39fAcCA3B4dC5854
///         Mechanics: pulls pUSD from `msg.sender`, mints 1:1 USDC.e into `_to`.
contract MockOfframp {
    IMintableERC20 public immutable usdce;
    IERC20 public immutable pusd;

    constructor(address _usdce, address _pusd) {
        usdce = IMintableERC20(_usdce);
        pusd = IERC20(_pusd);
    }

    function unwrap(address asset, address to, uint256 amount) external {
        require(asset == address(usdce), "MockOfframp: only usdce");
        require(pusd.transferFrom(msg.sender, address(this), amount), "unwrap pull failed");
        usdce.mint(to, amount);
    }
}

interface IMintableERC20 is IERC20 {
    function mint(address to, uint256 amount) external;
}
