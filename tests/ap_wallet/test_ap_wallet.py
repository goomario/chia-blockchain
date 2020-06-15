import asyncio
import time

import pytest

from src.simulator.simulator_protocol import FarmNewBlockProtocol
from src.types.peer_info import PeerInfo
from src.util.ints import uint16, uint32
from tests.setup_nodes import setup_simulators_and_wallets
from src.consensus.block_rewards import calculate_base_fee, calculate_block_reward
from src.wallet.ap_wallet.ap_wallet import APWallet
from src.wallet.ap_wallet.authoriser_wallet import AuthoriserWallet
from src.wallet.ap_wallet import ap_puzzles
from src.types.BLSSignature import BLSSignature
from blspy import PublicKey


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.get_event_loop()
    yield loop


class TestWalletSimulator:
    @pytest.fixture(scope="function")
    async def wallet_node(self):
        async for _ in setup_simulators_and_wallets(1, 1, {}):
            yield _

    @pytest.fixture(scope="function")
    async def two_wallet_nodes(self):
        async for _ in setup_simulators_and_wallets(
            1, 2, {"COINBASE_FREEZE_PERIOD": 0}
        ):
            yield _

    @pytest.fixture(scope="function")
    async def two_wallet_nodes_five_freeze(self):
        async for _ in setup_simulators_and_wallets(
            1, 2, {"COINBASE_FREEZE_PERIOD": 5}
        ):
            yield _

    @pytest.fixture(scope="function")
    async def three_sim_two_wallets(self):
        async for _ in setup_simulators_and_wallets(
            3, 2, {"COINBASE_FREEZE_PERIOD": 0}
        ):
            yield _

    async def time_out_assert(self, timeout: int, function, value, arg=None):
        start = time.time()
        while time.time() - start < timeout:
            if arg is None:
                function_result = await function()
            else:
                function_result = await function(arg)
            if value == function_result:
                return
            await asyncio.sleep(1)
        assert False

    @pytest.mark.asyncio
    async def test_ap_spend(self, two_wallet_nodes):
        num_blocks = 10
        full_nodes, wallets = two_wallet_nodes
        full_node_1, server_1 = full_nodes[0]
        wallet_node, server_2 = wallets[0]
        wallet_node_2, server_3 = wallets[1]
        wallet = wallet_node.wallet_state_manager.main_wallet
        wallet2 = wallet_node_2.wallet_state_manager.main_wallet

        ph = await wallet.get_new_puzzlehash()

        await server_2.start_client(PeerInfo("localhost", uint16(server_1._port)), None)
        await server_3.start_client(PeerInfo("localhost", uint16(server_1._port)), None)

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        funds = sum(
            [
                calculate_base_fee(uint32(i)) + calculate_block_reward(uint32(i))
                for i in range(1, num_blocks - 2)
            ]
        )

        await self.time_out_assert(15, wallet.get_confirmed_balance, funds)

        # Get pubkeys for creating the puzzle
        ap_pubkey_a = await wallet.get_new_pubkey()
        ap_wallet: APWallet = await APWallet.create_wallet_for_ap(
            wallet_node_2.wallet_state_manager, wallet2, ap_pubkey_a
        )
        ap_pubkey_b = ap_wallet.ap_info.my_pubkey

        auth_wallet: AuthoriserWallet = await AuthoriserWallet.create_wallet_for_ap(
            wallet_node.wallet_state_manager, wallet
        )

        ap_puz = ap_puzzles.ap_make_puzzle(ap_pubkey_a, ap_pubkey_b)

        await auth_wallet.add_ap_info("test_contact", ap_pubkey_a, ap_pubkey_b)
        auth_info = auth_wallet.get_ap_info()
        assert auth_info["test_contact"]["my_pubkey"] == bytes(ap_pubkey_a)
        assert auth_info["test_contact"]["their_pubkey"] == bytes(ap_pubkey_b)
        assert auth_info["test_contact"]["puzhash"] == ap_puz.get_tree_hash()

        sig = await auth_wallet.sign(ap_puz.get_tree_hash(), bytes(ap_pubkey_a))
        assert sig is not None
        await ap_wallet.set_sender_values(ap_pubkey_a, sig)
        assert ap_wallet.ap_info.change_signature is not None
        assert BLSSignature.from_bytes(ap_wallet.ap_info.change_signature).validate(
            [BLSSignature.PkMessagePair(ap_pubkey_a, ap_puz.get_tree_hash())]
        )
        assert BLSSignature.from_bytes(ap_wallet.ap_info.change_signature).validate(
            [
                BLSSignature.PkMessagePair(
                    PublicKey.from_bytes(ap_wallet.ap_info.authoriser_pubkey),
                    ap_puz.get_tree_hash(),
                )
            ]
        )
        tx = await wallet.generate_signed_transaction(100, ap_puz.get_tree_hash())
        await wallet.push_transaction(tx)

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        await self.time_out_assert(15, ap_wallet.get_confirmed_balance, 100)
        await self.time_out_assert(15, ap_wallet.get_unconfirmed_balance, 100)

        # Generate contact for ap_wallet

        ph2 = await wallet2.get_new_puzzlehash()
        sig = await wallet.sign(ph2, ap_pubkey_a)
        assert sig.validate([sig.PkMessagePair(ap_pubkey_a, ph2)])
        await ap_wallet.add_contact("wallet2", ph2, sig)

        # spend 20
        tx = await ap_wallet.ap_generate_signed_transaction(20, ph2)
        assert tx is not None

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        await self.time_out_assert(15, ap_wallet.get_confirmed_balance, 80)
        await self.time_out_assert(15, ap_wallet.get_unconfirmed_balance, 80)
        await self.time_out_assert(15, wallet2.get_confirmed_balance, 20)
        await self.time_out_assert(15, wallet2.get_unconfirmed_balance, 20)
    """
    @pytest.mark.asyncio
    async def test_siphon_value_from_spend(self, two_wallet_nodes):
        num_blocks = 10
        full_nodes, wallets = two_wallet_nodes
        full_node_1, server_1 = full_nodes[0]
        wallet_node, server_2 = wallets[0]
        wallet_node_2, server_3 = wallets[1]
        wallet = wallet_node.wallet_state_manager.main_wallet
        wallet2 = wallet_node_2.wallet_state_manager.main_wallet

        ph = await wallet.get_new_puzzlehash()

        await server_2.start_client(PeerInfo("localhost", uint16(server_1._port)), None)
        await server_3.start_client(PeerInfo("localhost", uint16(server_1._port)), None)

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        funds = sum(
            [
                calculate_base_fee(uint32(i)) + calculate_block_reward(uint32(i))
                for i in range(1, num_blocks - 2)
            ]
        )

        await self.time_out_assert(15, wallet.get_confirmed_balance, funds)

        # Get pubkeys for creating the puzzle
        devrec = await wallet.wallet_state_manager.get_unused_derivation_record(
            wallet.wallet_info.id
        )
        ap_pubkey_a = devrec.pubkey
        ap_wallet: APWallet = await APWallet.create_wallet_for_ap(
            wallet_node_2.wallet_state_manager, wallet2, ap_pubkey_a
        )
        ap_pubkey_b = ap_wallet.ap_info.my_pubkey

        ap_puz = ap_puzzles.ap_make_puzzle(ap_pubkey_a, ap_pubkey_b)
        sig = await wallet.sign(ap_puz.get_tree_hash(), bytes(ap_pubkey_a))
        assert sig is not None
        await ap_wallet.set_sender_values(ap_pubkey_a, sig)
        assert ap_wallet.ap_info.change_signature is not None
        assert BLSSignature.from_bytes(ap_wallet.ap_info.change_signature).validate(
            [BLSSignature.PkMessagePair(ap_pubkey_a, ap_puz.get_tree_hash())]
        )
        assert BLSSignature.from_bytes(ap_wallet.ap_info.change_signature).validate(
            [
                BLSSignature.PkMessagePair(
                    PublicKey.from_bytes(ap_wallet.ap_info.authoriser_pubkey),
                    ap_puz.get_tree_hash(),
                )
            ]
        )
        tx = await wallet.generate_signed_transaction(100, ap_puz.get_tree_hash())
        await wallet.push_transaction(tx)

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        await self.time_out_assert(15, ap_wallet.get_confirmed_balance, 100)
        await self.time_out_assert(15, ap_wallet.get_unconfirmed_balance, 100)

        # Try and spend some money with not all output accounted for

        ph2 = await wallet2.get_new_puzzlehash()
        sig = await wallet.sign(ph2, ap_pubkey_a)
        assert sig.validate([sig.PkMessagePair(ap_pubkey_a, ph2)])
        await ap_wallet.add_contact("wallet2", ph2, sig)

        amount = 20
        puzzlehash = ph2
        # calculate amount of transaction and change
        coins = await ap_wallet.select_coins(amount)
        assert coins is not None
        assert coins is not set()
        # We could take this out and just let the transaction fail, but its probably better to have the sanity check
        auth_sig = None
        for name_address in ap_wallet.ap_info.contacts:
            if puzzlehash == name_address[1]:
                auth_sig = BLSSignature.from_bytes(name_address[2])
                assert auth_sig.validate(
                    [
                        auth_sig.PkMessagePair(
                            PublicKey.from_bytes(ap_wallet.ap_info.authoriser_pubkey),
                            puzzlehash,
                        )
                    ]
                )
                break
        if auth_sig is None:
            return None

        sigs = [auth_sig]
        spends = []
        coin = coins.pop()
        solution = ap_puzzles.ap_make_solution(
            [(puzzlehash, amount)], coin.parent_coin_info, ap_puz.get_tree_hash()
        )
        spends.append((ap_puz, CoinSolution(coin, solution)))

        tx = await ap_wallet.ap_sign_transaction(spends, sigs)

        assert tx is not None

        await self.time_out_assert(15, ap_wallet.get_confirmed_balance, 100)
        await self.time_out_assert(15, ap_wallet.get_unconfirmed_balance, 100)

    @pytest.mark.asyncio
    async def test_ap_spend_multiple(self, two_wallet_nodes):
        num_blocks = 10
        full_nodes, wallets = two_wallet_nodes
        full_node_1, server_1 = full_nodes[0]
        wallet_node, server_2 = wallets[0]
        wallet_node_2, server_3 = wallets[1]
        wallet = wallet_node.wallet_state_manager.main_wallet
        wallet2 = wallet_node_2.wallet_state_manager.main_wallet

        ph = await wallet.get_new_puzzlehash()

        await server_2.start_client(PeerInfo("localhost", uint16(server_1._port)), None)
        await server_3.start_client(PeerInfo("localhost", uint16(server_1._port)), None)

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        funds = sum(
            [
                calculate_base_fee(uint32(i)) + calculate_block_reward(uint32(i))
                for i in range(1, num_blocks - 2)
            ]
        )

        await self.time_out_assert(15, wallet.get_confirmed_balance, funds)

        # Get pubkeys for creating the puzzle
        devrec = await wallet.wallet_state_manager.get_unused_derivation_record(
            wallet.wallet_info.id
        )
        ap_pubkey_a = devrec.pubkey
        ap_wallet: APWallet = await APWallet.create_wallet_for_ap(
            wallet_node_2.wallet_state_manager, wallet2, ap_pubkey_a
        )
        ap_pubkey_b = ap_wallet.ap_info.my_pubkey

        ap_puz = ap_puzzles.ap_make_puzzle(ap_pubkey_a, ap_pubkey_b)
        sig = await wallet.sign(ap_puz.get_tree_hash(), bytes(ap_pubkey_a))
        assert sig is not None
        await ap_wallet.set_sender_values(ap_pubkey_a, sig)
        assert ap_wallet.ap_info.change_signature is not None
        assert BLSSignature.from_bytes(ap_wallet.ap_info.change_signature).validate(
            [BLSSignature.PkMessagePair(ap_pubkey_a, ap_puz.get_tree_hash())]
        )
        assert BLSSignature.from_bytes(ap_wallet.ap_info.change_signature).validate(
            [
                BLSSignature.PkMessagePair(
                    PublicKey.from_bytes(ap_wallet.ap_info.authoriser_pubkey),
                    ap_puz.get_tree_hash(),
                )
            ]
        )
        tx = await wallet.generate_signed_transaction(100, ap_puz.get_tree_hash())
        await wallet.push_transaction(tx)

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        await self.time_out_assert(15, ap_wallet.get_confirmed_balance, 100)
        await self.time_out_assert(15, ap_wallet.get_unconfirmed_balance, 100)
        tx = await wallet.generate_signed_transaction(50, ap_puz.get_tree_hash())
        await wallet.push_transaction(tx)

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        await self.time_out_assert(15, ap_wallet.get_confirmed_balance, 150)
        await self.time_out_assert(15, ap_wallet.get_unconfirmed_balance, 150)

        tx = await wallet.generate_signed_transaction(25, ap_puz.get_tree_hash())
        await wallet.push_transaction(tx)

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        await self.time_out_assert(15, ap_wallet.get_confirmed_balance, 175)
        await self.time_out_assert(15, ap_wallet.get_unconfirmed_balance, 175)

        # Generate contact for ap_wallet

        ph2 = await wallet2.get_new_puzzlehash()
        sig = await wallet.sign(ph2, ap_pubkey_a)
        assert sig.validate([sig.PkMessagePair(ap_pubkey_a, ph2)])
        await ap_wallet.add_contact("wallet2", ph2, sig)

        tx = await ap_wallet.ap_generate_signed_transaction(170, ph2)
        assert tx is not None

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        await self.time_out_assert(15, ap_wallet.get_confirmed_balance, 5)
        await self.time_out_assert(15, ap_wallet.get_unconfirmed_balance, 5)
        await self.time_out_assert(15, wallet2.get_confirmed_balance, 170)
        await self.time_out_assert(15, wallet2.get_unconfirmed_balance, 170)

    @pytest.mark.asyncio
    async def test_ap_spend_invalid(self, two_wallet_nodes):
        num_blocks = 10
        full_nodes, wallets = two_wallet_nodes
        full_node_1, server_1 = full_nodes[0]
        wallet_node, server_2 = wallets[0]
        wallet_node_2, server_3 = wallets[1]
        wallet = wallet_node.wallet_state_manager.main_wallet
        wallet2 = wallet_node_2.wallet_state_manager.main_wallet

        ph = await wallet.get_new_puzzlehash()

        await server_2.start_client(PeerInfo("localhost", uint16(server_1._port)), None)
        await server_3.start_client(PeerInfo("localhost", uint16(server_1._port)), None)

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        funds = sum(
            [
                calculate_base_fee(uint32(i)) + calculate_block_reward(uint32(i))
                for i in range(1, num_blocks - 2)
            ]
        )

        await self.time_out_assert(15, wallet.get_confirmed_balance, funds)

        # Get pubkeys for creating the puzzle
        devrec = await wallet.wallet_state_manager.get_unused_derivation_record(
            wallet.wallet_info.id
        )
        ap_pubkey_a = devrec.pubkey
        ap_wallet: APWallet = await APWallet.create_wallet_for_ap(
            wallet_node_2.wallet_state_manager, wallet2, ap_pubkey_a
        )
        ap_pubkey_b = ap_wallet.ap_info.my_pubkey

        ap_puz = ap_puzzles.ap_make_puzzle(ap_pubkey_a, ap_pubkey_b)
        sig = await wallet.sign(ap_puz.get_tree_hash(), bytes(ap_pubkey_a))
        assert sig is not None
        await ap_wallet.set_sender_values(ap_pubkey_a, sig)
        assert ap_wallet.ap_info.change_signature is not None
        assert BLSSignature.from_bytes(ap_wallet.ap_info.change_signature).validate(
            [BLSSignature.PkMessagePair(ap_pubkey_a, ap_puz.get_tree_hash())]
        )
        assert BLSSignature.from_bytes(ap_wallet.ap_info.change_signature).validate(
            [
                BLSSignature.PkMessagePair(
                    PublicKey.from_bytes(ap_wallet.ap_info.authoriser_pubkey),
                    ap_puz.get_tree_hash(),
                )
            ]
        )
        tx = await wallet.generate_signed_transaction(100, ap_puz.get_tree_hash())
        await wallet.push_transaction(tx)

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        await self.time_out_assert(15, ap_wallet.get_confirmed_balance, 100)
        await self.time_out_assert(15, ap_wallet.get_unconfirmed_balance, 100)

        ph2 = await wallet2.get_new_puzzlehash()

        amount = 100
        puzzlehash = ph2
        # calculate amount of transaction and change
        coins = await ap_wallet.select_coins(amount)
        assert coins is not None
        assert coins is not set()
        spends = []
        coin = coins.pop()
        solution = ap_puzzles.ap_make_solution(
            [(puzzlehash, amount)], coin.parent_coin_info, ap_puz.get_tree_hash()
        )
        spends.append((ap_puz, CoinSolution(coin, solution)))

        tx = await ap_wallet.ap_sign_transaction(spends, [])

        assert tx is not None

        for i in range(1, num_blocks):
            await full_node_1.farm_new_block(FarmNewBlockProtocol(ph))

        await self.time_out_assert(15, ap_wallet.get_confirmed_balance, 100)
        await self.time_out_assert(15, ap_wallet.get_unconfirmed_balance, 100)
    """
