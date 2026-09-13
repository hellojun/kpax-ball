// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";

import {LendingVaultV2} from "../src/LendingVaultV2.sol";
import {IPolymarketExchange} from "../src/interfaces/IPolymarketExchange.sol";

/// @notice Direct V2 deploy — no TimelockController in front of the upgrader.
///         The admin EOA is the upgrader, which means upgrades land in one
///         tx with zero delay. Trade-off: the admin private key is the only
///         line of defence; if it leaks, an attacker can swap the
///         implementation immediately.
///
///         Used for the gradual-rollout phase (small TVL, dev iteration on
///         contract logic). When the project graduates to real users with
///         meaningful TVL, deploy a Safe multisig + TimelockController
///         and call `vault.setUpgrader(timelock)` to hand governance back.
///
///         Reuses an already-deployed V2 implementation (passed in as
///         `existingImpl`); does NOT redeploy the implementation contract.
///         If you want a fresh impl too, set `existingImpl = address(0)`.
///
/// Polygon-mainnet defaults are baked in (USDC / Polymarket CTF / Exchange).
///
/// Required env:
///   ADMIN_ADDRESS   — vault admin + upgrader (same EOA in dev)
///   KEEPER_ADDRESS  — keeper EOA (off-chain liquidator)
///
/// Usage:
///   forge script script/DeployV2Direct.s.sol:DeployV2Direct \
///     --sig "run(address)" 0xC793D31Aa6c8A0E26AA20b61a7C572b8EB061C6a \
///     --rpc-url https://polygon-bor-rpc.publicnode.com \
///     --account deployer --sender 0xYourAdmin \
///     --broadcast --verify --etherscan-api-key $POLYGONSCAN_KEY -vvv
///
/// Output: prints the new proxy address — write this into backend/.env
/// as KPAX_VAULT_ADDRESS.
contract DeployV2Direct is Script {
    address constant POLYGON_USDC = 0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359;
    address constant POLYGON_POLYMARKET_CTF =
        0x4D97DCd97eC945f40cF65F87097ACe5EA0476045;
    address constant POLYGON_POLYMARKET_EXCHANGE =
        0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E;

    function run(address existingImpl)
        external
        returns (LendingVaultV2 impl, ERC1967Proxy proxy)
    {
        address admin = _envOrSender("ADMIN_ADDRESS");
        address keeper = _envOrSender("KEEPER_ADDRESS");

        console2.log("== KPAX LendingVault V2 direct deploy (NO timelock) ==");
        console2.log("usdc        :", POLYGON_USDC);
        console2.log("ctf         :", POLYGON_POLYMARKET_CTF);
        console2.log("exchange    :", POLYGON_POLYMARKET_EXCHANGE);
        console2.log("admin       :", admin);
        console2.log("keeper      :", keeper);
        console2.log("upgrader    :", admin, "(SAME AS ADMIN, no timelock)");

        vm.startBroadcast();

        if (existingImpl == address(0)) {
            impl = new LendingVaultV2();
            console2.log("New impl deployed at:", address(impl));
        } else {
            impl = LendingVaultV2(existingImpl);
            console2.log("Reusing existing impl:", existingImpl);
        }

        bytes memory initData = abi.encodeCall(
            LendingVaultV2.initialize,
            (
                IERC20(POLYGON_USDC),
                IERC1155(POLYGON_POLYMARKET_CTF),
                IPolymarketExchange(POLYGON_POLYMARKET_EXCHANGE),
                admin,
                keeper,
                admin // upgrader == admin (no delay)
            )
        );
        proxy = new ERC1967Proxy(address(impl), initData);

        vm.stopBroadcast();

        console2.log("");
        console2.log("== Deploy summary ==");
        console2.log("USE THIS in backend/.env:");
        console2.log("  KPAX_VAULT_ADDRESS =", address(proxy));
        console2.log("");
        console2.log("Next admin actions (no delay, can do all in one session):");
        console2.log("  1. vault.setTreasury(0xcb7A9e4F9366de25F36195Ce96625bE3dEe05bb7)");
        console2.log("  2. usdc.approve(vault, X) + vault.depositLP(X)");
        console2.log("  3. keeper EOA: ctf.setApprovalForAll(vault, true)");
        console2.log("  4. keeper EOA: ctf.setApprovalForAll(POLYMARKET_EXCHANGE, true)");
        console2.log("  5. wind down OLD vault 0x40892387747Da2f46fecaC902274081F676F1592 separately");
    }

    function _envOrSender(string memory key) internal view returns (address) {
        try vm.envAddress(key) returns (address v) {
            return v;
        } catch {
            return msg.sender;
        }
    }
}
