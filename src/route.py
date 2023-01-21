import math
from dataclasses import dataclass, field

from cosmpy.aerial.tx import Transaction as Tx, SigningCfg
from cosmpy.protos.cosmos.bank.v1beta1.tx_pb2 import MsgSend
from cosmpy.protos.cosmos.base.v1beta1.coin_pb2 import Coin

from src.contract import Pool
from src.swap import Swap, calculate_swap


@dataclass 
class Route:
    pools: list[Pool] = field(default_factory=list)
    profit: int = 0
    optimal_amount_in: int = 0
    amount_in: int = 0
    
    def order_pools(self,
                    contracts: dict, 
                    swap: Swap, 
                    arb_denom: str) -> None:
        """Given a swap and self, reorder the self so that the
           swap is in the opposite direction of the self.
        """        
        # Get the index of the pool swapped against in the route 
        swapped_self_index = self.pools.index(
                                contracts[swap.contract_address]
                                )
        # Set our input denom to the output denom of the swap
        # We swap in the opposite direction as the original swap
        input_denom = swap.output_denom
        # Order the route based on the index of the swapped pool
        match(swapped_self_index):
            case 0:
                self._order_first_pool(input_denom=input_denom, 
                                       arb_denom=arb_denom)
            case 1:
                self._order_second_pool(contracts=contracts, 
                                        input_denom=input_denom, 
                                        arb_denom=arb_denom)
            case 2:
                self._order_last_pool(input_denom=input_denom, 
                                      arb_denom=arb_denom)

    def _order_first_pool(self,
                          input_denom: str,
                          arb_denom: str):      
        """ Order route based on 1st pool."""
        if input_denom != arb_denom:
            self.pools.reverse()
            
    def _order_second_pool(self,
                           contracts: dict,
                           input_denom: str,
                           arb_denom: str):
        """ Order route based on 2nd pool."""
        first_pool = self.pools[0]
        
        if first_pool.token1_denom != arb_denom:
            output_denom = first_pool.token1_denom
        else:
            output_denom = first_pool.token2_denom

        if input_denom != output_denom:
            self.pools.reverse()
            
    def _order_last_pool(self,
                         input_denom: str,
                         arb_denom: str):
        """ Order route based on 3rd pool."""
        if input_denom == arb_denom:
            self.pools.reverse()
            
    def calculate_and_set_profit(self) -> int:
        """ Calculate the profit of the self."""
        # Iterate through the pools and calculate the amount out
        # until the last pool, then calculate and set the profit
        for i, pool in enumerate(self.pools):
            if i == 0:
                pool.amount_in = self.amount_in
            else:
                pool.amount_in = self.pools[i-1].amount_out
                
            pool.amount_out, _, _ = calculate_swap(
                                        reserves_in=pool.input_reserves,
                                        reserves_out=pool.output_reserves,
                                        amount_in=pool.amount_in,
                                        lp_fee=pool.lp_fee,
                                        protocol_fee=pool.protocol_fee,
                                        fee_from_input=pool.fee_from_input
                                        )
        
        self.profit = self.pools[-1].amount_out - self.pools[0].amount_in
        return self.profit
    
    def calculate_and_set_optimal_amount_in(self) -> None:
        """Given an ordered route, calculates and sets the
           optimal amount to swap into the first pool, by 
           implementing this paper: https://arxiv.org/abs/2105.02784
           for three pool cyclic arbitrage.
        """
        # lists of input and output reserves and fees
        input_reserves, output_reserves, input_fees, output_fees = [], [], [], []
        # Append the reserves and fees to the lists
        for pool in self.pools:
            input_reserves.append(pool.input_reserves)
            output_reserves.append(pool.output_reserves)
            if pool.fee_from_input:
                input_fees.append(1 - (pool.lp_fee + pool.protocol_fee))
                output_fees.append(1)
            else:
                input_fees.append(1)
                output_fees.append(1 - (pool.lp_fee + pool.protocol_fee))
        # Set a_prime_in and a_prime_out as the first pool reserves
        a_prime_in = input_reserves[0]
        a_prime_out = output_reserves[0]
        # Iterate through the pools and calculate a_prime_in and a_prime_out
        for in_res, out_res, in_fee, out_fee in zip(input_reserves[1:], 
                                                    output_reserves[1:], 
                                                    input_fees[1:], 
                                                    output_fees[1:]):
            a_prime_in = (a_prime_in * in_res) / (in_res + (in_fee * out_fee * a_prime_out))
            a_prime_out = (in_fee * out_fee * out_res * a_prime_out) / (in_res + (in_fee * out_fee * a_prime_out))
        # Set optimal amount in
        self.optimal_amount_in = math.floor(
                                    (math.sqrt(input_fees[0] * output_fees[0] * a_prime_out * a_prime_in) - a_prime_in)
                                    / (input_fees[0]))
        
    def calculate_and_set_amount_in(self,
                                    account_balance: int,
                                    gas_fee: int) -> None:
        """ Set the amount to swap into the first pool"""
        if self.optimal_amount_in <= 0:
            pass
        elif self.optimal_amount_in > account_balance - gas_fee:
            self.amount_in = account_balance - gas_fee
        else:
            self.amount_in = self.optimal_amount_in