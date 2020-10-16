from secrets import token_bytes

from src.full_node.full_node import FullNode
from typing import AsyncGenerator, List, Optional

from src.full_node.full_node_api import FullNodeAPI
from src.protocols import (
    full_node_protocol,
)
from src.server.ws_connection import WSChiaConnection
from src.simulator.simulator_protocol import FarmNewBlockProtocol, ReorgProtocol
from src.full_node.bundle_tools import best_solution_program
from src.server.outbound_message import OutboundMessage
from src.types.full_block import FullBlock
from src.types.spend_bundle import SpendBundle
from src.types.header import Header
from src.util.api_decorators import api_request
from src.util.ints import uint64


OutboundMessageGenerator = AsyncGenerator[OutboundMessage, None]


class FullNodeSimulator(FullNodeAPI):
    def __init__(self, full_node, bt):
        super().__init__(full_node)
        self.full_node = full_node
        self.bt = bt

    # WALLET LOCAL TEST PROTOCOL
    def get_tip(self):
        tips = self.full_node.blockchain.tips
        top = tips[0]

        for tip in tips:
            if tip.height > top.height:
                top = tip

        return top

    async def get_current_blocks(self, tip: Header) -> List[FullBlock]:

        current_blocks: List[FullBlock] = []
        tip_hash = tip.header_hash

        while True:
            if tip_hash == self.full_node.blockchain.genesis.header_hash:
                current_blocks.append(self.full_node.blockchain.genesis)
                break
            full = await self.full_node.block_store.get_block(tip_hash)
            if full is None:
                break
            current_blocks.append(full)
            tip_hash = full.prev_header_hash

        current_blocks.reverse()
        return current_blocks

    @api_request
    async def farm_new_block(
        self, request: FarmNewBlockProtocol, peer: WSChiaConnection
    ):
        self.full_node.log.info("Farming new block!")
        top_tip = self.get_tip()
        if top_tip is None or self.full_node.server is None:
            return

        current_block = await self.get_current_blocks(top_tip)
        bundle: Optional[
            SpendBundle
        ] = await self.full_node.mempool_manager.create_bundle_for_tip(top_tip)

        dict_h = {}
        fees = 0
        if bundle is not None:
            program = best_solution_program(bundle)
            dict_h[top_tip.height + 1] = (program, bundle.aggregated_signature)
            fees = bundle.fees()

        more_blocks = self.bt.get_consecutive_blocks(
            self.full_node.constants,
            1,
            current_block,
            10,
            reward_puzzlehash=request.puzzle_hash,
            transaction_data_at_height=dict_h,
            seed=token_bytes(),
            fees=uint64(fees),
        )
        new_lca = more_blocks[-1]

        assert self.full_node.server is not None
        await self.full_node._respond_block(full_node_protocol.RespondBlock(new_lca))

    @api_request
    async def reorg_from_index_to_new_index(
        self, request: ReorgProtocol, peer: WSChiaConnection
    ):
        new_index = request.new_index
        old_index = request.old_index
        coinbase_ph = request.puzzle_hash
        top_tip = self.get_tip()

        current_blocks = await self.get_current_blocks(top_tip)
        block_count = new_index - old_index

        more_blocks = self.bt.get_consecutive_blocks(
            self.full_node.constants,
            block_count,
            current_blocks[:old_index],
            10,
            seed=token_bytes(),
            reward_puzzlehash=coinbase_ph,
            transaction_data_at_height={},
        )

        assert self.full_node.server is not None
        for block in more_blocks:
            await self.full_node._respond_block(full_node_protocol.RespondBlock(block))
