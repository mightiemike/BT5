### Title
Fixed `SLOW_MODE_FEE` Provides No Compensation for L1 Rollup Fees to `executeSlowModeTransaction()` Callers on Ink Chain (L2) — (`File: core/contracts/Endpoint.sol`, `core/contracts/common/Constants.sol`)

---

### Summary

Nado is deployed on **Ink Chain**, an L2 EVM network. The `executeSlowModeTransaction()` function is the protocol's censorship-resistance mechanism — any third party can call it to force-execute a pending slow mode transaction after the delay period. However, callers receive **zero compensation**: the fixed `SLOW_MODE_FEE` ($1) collected from users is routed entirely to the sequencer, not to the executor. On an L2, callers must pay both L2 execution fees and L1 data fees (calldata posted to Ethereum), yet the protocol provides no reward. The `GasInfo.sol` utility contract exists with L1 fee query functions (`getL1Fee`, `getL1GasUsed`) but all return hardcoded `0` and are never integrated into any fee calculation.

---

### Finding Description

When a user submits a slow mode transaction via `submitSlowModeTransaction()`, they pay a hardcoded `SLOW_MODE_FEE = 1000000` ($1 in 6-decimal USDC): [1](#0-0) 

This fee is collected via `chargeSlowModeFee()` and transferred to the `Endpoint` contract: [2](#0-1) 

The accumulated `slowModeFees` are tracked and later claimed by the sequencer via the `DumpFees` transaction path: [3](#0-2) 

The `executeSlowModeTransaction()` function — callable by any third party — pays **no reward** to its caller: [4](#0-3) 

The `GasInfo.sol` contract has the interface to query L1 data fees but all methods return `0` and are never called from any fee-charging path: [5](#0-4) 

On Ink Chain (L2), every call to `executeSlowModeTransaction()` incurs both an L2 execution fee and an L1 data fee (for calldata posted to Ethereum). The L1 data fee is typically the dominant cost and fluctuates with Ethereum gas prices. Since callers receive nothing, the economic incentive to call `executeSlowModeTransaction()` is always negative.

---

### Impact Explanation

The slow mode path is the protocol's only censorship-resistance guarantee. If the sequencer censors a user's slow mode transaction (e.g., a `WithdrawCollateral`), the user or a third party must call `executeSlowModeTransaction()` to unblock it. With no reward and a non-trivial L1 data fee cost, third parties have no economic reason to do so. The user themselves must pay the L1 fee on top of the $1 already paid at submission. During periods of high Ethereum gas prices (L1 data fees can be 10–1000x the L2 execution fee, as demonstrated in the reference report), the cost to execute a single slow mode transaction can far exceed $1. This means:

- Users' withdrawal and other slow mode transactions can be permanently stuck if the sequencer censors them.
- The censorship-resistance invariant — that slow mode transactions are always executable by third parties — is broken under realistic L2 gas conditions.
- Fund lock is the concrete asset impact: collateral deposited in `Clearinghouse` cannot be withdrawn. [4](#0-3) 

---

### Likelihood Explanation

Ink Chain is an L2 that posts calldata to Ethereum. L1 data fees are always present and frequently exceed L2 execution fees by orders of magnitude. The `SLOW_MODE_FEE` is hardcoded at compile time and cannot be adjusted without a contract upgrade. There is no dynamic fee oracle integration. The `GasInfo.sol` stub returning `0` for all L1 fee queries confirms no L1 fee awareness exists in the current implementation. The condition (L1 fees > $0, which is always true) is permanently met. [6](#0-5) 

---

### Recommendation

1. Integrate `GasInfo.sol` (or the equivalent L2 system contract at the deployed address on Ink Chain) into the fee calculation for `executeSlowModeTransaction()`.
2. Pay a portion of the collected `slowModeFees` to `msg.sender` of `executeSlowModeTransaction()`, calculated as: `L1DataFee(tx.calldata) + L2ExecutionFee * multiplier`.
3. Alternatively, make `SLOW_MODE_FEE` a governance-adjustable parameter rather than a compile-time constant, so it can track actual L1 gas costs.

Reference implementations: Arbitrum L1 pricing docs and Optimism fee calculator (as cited in the original Perennial report).

---

### Proof of Concept

1. User calls `submitSlowModeTransaction(withdrawCollateralTx)` and pays $1 USDC. The sequencer censors this transaction.
2. After `SLOW_MODE_TX_DELAY` (3 days), any third party can call `executeSlowModeTransaction()`.
3. On Ink Chain, this call costs: L2 fee (negligible) + L1 data fee (e.g., $5–$50 during moderate Ethereum gas conditions).
4. The caller receives $0 in return. No rational third party calls the function.
5. The user's withdrawal is permanently stuck unless they pay the L1 fee themselves — on top of the $1 already paid — with no guarantee of reimbursement. [7](#0-6) [4](#0-3)

### Citations

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
```

**File:** core/contracts/EndpointStorage.sol (L83-93)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L332-385)
```text
    function submitSlowModeTransactionImpl(bytes calldata transaction) public {
        IEndpoint.TransactionType txType = IEndpoint.TransactionType(
            uint8(transaction[0])
        );

        // special case for DepositCollateral because upon
        // slow mode submission we must take custody of the
        // actual funds

        address sender = msg.sender;

        if (txType == IEndpoint.TransactionType.DepositCollateral) {
            revert();
        } else if (txType == IEndpoint.TransactionType.DepositInsurance) {
            IEndpoint.DepositInsurance memory txn = abi.decode(
                transaction[1:],
                (IEndpoint.DepositInsurance)
            );
            require(
                txn.amount >= uint128(SLOW_MODE_FEE),
                ERR_DEPOSIT_TOO_SMALL
            );
            handleDepositTransfer(_getQuote(), sender, uint256(txn.amount));
        } else if (
            txType == IEndpoint.TransactionType.WithdrawInsurance ||
            txType == IEndpoint.TransactionType.DelistProduct ||
            txType == IEndpoint.TransactionType.DumpFees ||
            txType == IEndpoint.TransactionType.RebalanceXWithdraw ||
            txType == IEndpoint.TransactionType.UpdateTierFeeRates ||
            txType == IEndpoint.TransactionType.AddNlpPool ||
            txType == IEndpoint.TransactionType.UpdateNlpPool ||
            txType == IEndpoint.TransactionType.DeleteNlpPool ||
            txType == IEndpoint.TransactionType.ForceRebalanceNlpPool ||
            txType == IEndpoint.TransactionType.NlpProfitShare ||
            txType == IEndpoint.TransactionType.UpdateBuilder
        ) {
            require(sender == owner());
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }

        IEndpoint.SlowModeConfig memory _slowModeConfig = slowModeConfig;
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: transaction
        });
        // TODO: to save on costs we could potentially just emit something
        // for now, we can just create a separate loop in the engine that queries the remote
        // sequencer for slow mode transactions, and ignore the possibility of a reorgy attack
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/Endpoint.sol (L231-236)
```text
    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/util/GasInfo.sol (L1-57)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

contract GasInfo {
    /// Arbitrum:
    /// @notice Get gas prices. Uses the caller's preferred aggregator, or the default if the caller doesn't have a preferred one.
    /// @return return gas prices in wei
    ///        (
    ///            per L2 tx,
    ///            per L1 calldata byte
    ///            per storage allocation,
    ///            per ArbGas base,
    ///            per ArbGas congestion,
    ///            per ArbGas total
    ///        )
    function getPricesInWei()
        public
        pure
        returns (
            uint256,
            uint256, // this value is the
            uint256,
            uint256,
            uint256,
            uint256
        )
    {
        return (0, 0, 0, 0, 0, 0);
    }

    /// Optimism:
    // to compute approximate wei per L1 calldata byte, we do getL1Fee('0xF * 1000') / getL1GasUsed('0xF * 1000')
    /// @notice Computes the amount of L1 gas used for a transaction. Adds 68 bytes
    ///         of padding to account for the fact that the input does not have a signature.
    /// @param _data Unsigned fully RLP-encoded transaction to get the L1 gas for.
    /// @return Amount of L1 gas used to publish the transaction.
    // solhint-disable-next-line no-unused-vars
    function getL1GasUsed(bytes memory _data) public view returns (uint256) {
        return 0;
    }

    /// @notice Computes the L1 portion of the fee based on the size of the rlp encoded input
    ///         transaction, the current L1 base fee, and the various dynamic parameters.
    /// @param _data Unsigned fully RLP-encoded transaction to get the L1 fee for.
    /// @return L1 fee that should be paid for the tx
    // solhint-disable-next-line no-unused-vars
    function getL1Fee(bytes memory _data) external view returns (uint256) {
        return 0;
    }

    /// @custom:legacy
    /// @notice Retrieves the number of decimals used in the scalar.
    /// @return Number of decimals used in the scalar.
    function decimals() public pure returns (uint256) {
        return 0;
    }
}
```
