// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC1155} from "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import {ERC1967Proxy} from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Proxy.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

import {LendingVault} from "../src/LendingVault.sol";
import {IPolymarketExchange} from "../src/interfaces/IPolymarketExchange.sol";

/// @notice Deploy the upgradeable KPAX LendingVault to Polygon mainnet.
///
/// Stack:
///   - Implementation: vanilla LendingVault (UUPS).
///   - Proxy: ERC1967Proxy holding all storage; this is the address users interact with.
///   - TimelockController: 24h delay, set as `upgrader` on the vault.
///
/// Roles in the timelock:
///   - PROPOSER_ROLE / CANCELLER_ROLE: Safe 2-of-3 multisig (or `MULTISIG_ADDRESS` env).
///     If MULTISIG_ADDRESS is unset, defaults to broadcaster (EOA placeholder — see DEPLOY.md
///     for instructions on swapping to a real multisig later).
///   - EXECUTOR_ROLE: same multisig (we don't use the public-executor pattern).
///   - admin (TimelockController constructor's 4th arg): zero address — we want the
///     timelock to be self-administered from day one. No external admin can
///     short-circuit the delay.
///
/// Pass `--account <keystore>` to use a Foundry keystore (recommended).
///
/// Optional env overrides:
///   - ADMIN_ADDRESS       : vault admin (multisig owner)        — defaults to broadcaster
///   - KEEPER_ADDRESS      : vault keeper (off-chain liquidator) — defaults to broadcaster
///   - MULTISIG_ADDRESS    : timelock proposer/executor          — defaults to broadcaster
///   - TIMELOCK_DELAY      : seconds, defaults to 86400 (24h)
///   - USDC_ADDRESS / POLYMARKET_CTF_ADDRESS / POLYMARKET_EXCHANGE_ADDRESS
///
/// Usage:
///   cd contracts
///   forge script script/Deploy.s.sol:Deploy \
///     --rpc-url https://polygon-bor-rpc.publicnode.com \
///     --account deployer --sender 0xYourEOA \
///     --broadcast --verify --etherscan-api-key $POLYGONSCAN_KEY
contract Deploy is Script {
    // Polygon mainnet defaults
    address constant POLYGON_USDC = 0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359;
    address constant POLYGON_POLYMARKET_CTF =
        0x4D97DCd97eC945f40cF65F87097ACe5EA0476045;
    address constant POLYGON_POLYMARKET_EXCHANGE =
        0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E;

    uint256 constant DEFAULT_TIMELOCK_DELAY = 24 hours;

    function run()
        external
        returns (
            LendingVault impl,
            ERC1967Proxy proxy,
            TimelockController timelock
        )
    {
        address usdc = _envOrDefault("USDC_ADDRESS", POLYGON_USDC);
        address ctf = _envOrDefault(
            "POLYMARKET_CTF_ADDRESS", POLYGON_POLYMARKET_CTF
        );
        address exchange = _envOrDefault(
            "POLYMARKET_EXCHANGE_ADDRESS", POLYGON_POLYMARKET_EXCHANGE
        );
        address admin = _envOrDefault("ADMIN_ADDRESS", msg.sender);
        address keeper = _envOrDefault("KEEPER_ADDRESS", msg.sender);
        address multisig = _envOrDefault("MULTISIG_ADDRESS", msg.sender);
        uint256 delay = _envOrDefaultUint("TIMELOCK_DELAY", DEFAULT_TIMELOCK_DELAY);

        console2.log("== KPAX LendingVault deploy (UUPS proxy) ==");
        console2.log("usdc          :", usdc);
        console2.log("ctf           :", ctf);
        console2.log("exchange      :", exchange);
        console2.log("admin         :", admin);
        console2.log("keeper        :", keeper);
        console2.log("multisig      :", multisig);
        console2.log("timelockDelay :", delay);

        vm.startBroadcast();

        // 1. Deploy implementation (constructor disables initializers on impl itself)
        impl = new LendingVault();
        console2.log("impl deployed at:", address(impl));

        // 2. Deploy timelock with multisig as proposer/canceller/executor.
        //    admin = address(0) → self-administered, no super-admin override.
        address[] memory proposers = new address[](1);
        proposers[0] = multisig;
        address[] memory executors = new address[](1);
        executors[0] = multisig;
        timelock = new TimelockController(
            delay,
            proposers,
            executors,
            address(0) // no extra admin
        );
        console2.log("timelock deployed at:", address(timelock));

        // 3. Deploy proxy and atomically initialize. Using the timelock as
        //    the upgrader — only timelocked operations can swap impl.
        bytes memory initData = abi.encodeCall(
            LendingVault.initialize,
            (
                IERC20(usdc),
                IERC1155(ctf),
                IPolymarketExchange(exchange),
                admin,
                keeper,
                address(timelock)
            )
        );
        proxy = new ERC1967Proxy(address(impl), initData);
        console2.log("proxy deployed at:", address(proxy));

        vm.stopBroadcast();

        console2.log("");
        console2.log("== Deploy summary ==");
        console2.log("USE THIS in backend/.env:");
        console2.log("  KPAX_VAULT_ADDRESS =", address(proxy));
        console2.log("");
        console2.log("Implementation (do NOT use this for app calls):", address(impl));
        console2.log("Timelock (governance only):", address(timelock));
    }

    function _envOrDefault(string memory key, address fallbackValue)
        internal
        view
        returns (address)
    {
        try vm.envAddress(key) returns (address v) {
            return v;
        } catch {
            return fallbackValue;
        }
    }

    function _envOrDefaultUint(string memory key, uint256 fallbackValue)
        internal
        view
        returns (uint256)
    {
        try vm.envUint(key) returns (uint256 v) {
            return v;
        } catch {
            return fallbackValue;
        }
    }
}
