### Title
Missing Incentive to Execute Slow-Mode Transactions Leaves Deposited Collateral Indefinitely Stuck - (File: `core/contracts/Endpoint.sol`)

---

### Summary

The Nado slow-mode queue is the protocol's only on-chain censorship-resistance mechanism. Users who call `depositCollateral` have their ERC-20 tokens immediately transferred to the clearinghouse, but their on-chain balance credit is deferred to a queued `SlowModeTx`. The public `executeSlowModeTransaction()` function that processes this queue provides **zero reward** to its caller. The `SLOW_MODE_FEE` paid by submitters accrues entirely to the protocol. When the sequencer is offline or censoring — the exact scenario slow mode is designed for — there is no on-chain incentive for any external actor to drain the queue, leaving deposited user funds permanently inaccessible.

---

### Finding Description

When a user calls `depositCollateral` or `depositCollateralWithReferral`, their ERC-20 tokens are immediately and irrevocably transferred to the clearinghouse contract via `handleDepositTransfer`: [1](#0-0) 

Immediately after, a `SlowModeTx` is enqueued with a hardcoded 3-day delay before it becomes executable: [2](#0-1) 

The on-chain balance credit only occurs when `processSlowModeTransactionImpl` is eventually called — which requires someone to invoke `executeSlowModeTransaction()`: [3](#0-2) 

This function processes exactly one transaction per call, in strict FIFO order (`txUpTo`), and provides **no reward whatsoever** to the caller. The `SLOW_MODE_FEE` ($1 USDC, defined as `SLOW_MODE_FEE = 1000000`) paid by submitters of other slow-mode transaction types is collected via `chargeSlowModeFee` and accumulated into `slowModeFees`: [4](#0-3) 

This fee goes to the protocol, not to the executor: [5](#0-4) 

The sequencer is the primary executor of slow-mode transactions (via `processTransaction` with `TransactionType.ExecuteSlowMode`): [6](#0-5) 

But slow mode is explicitly designed for the scenario where the sequencer is **unavailable or censoring**. In that scenario, the only fallback is the public `executeSlowModeTransaction()` — which has no embedded incentive. The 3-day delay constant confirms this is a deliberate censorship-resistance path: [7](#0-6) 

---

### Impact Explanation

A user who calls `depositCollateral` has already surrendered custody of their ERC-20 tokens to the clearinghouse. Their on-chain balance is not credited until the queued `SlowModeTx` is executed. If the sequencer is offline and no external actor is incentivized to call `executeSlowModeTransaction()`, the user's funds are stuck indefinitely. The FIFO queue compounds this: a user must pay gas to execute every transaction ahead of theirs before their own deposit is credited. Similarly, slow-mode `WithdrawCollateral` requests — the last resort for a censored user to recover funds — will never be processed. The corrupted state is the user's on-chain balance in `SpotEngine`, which remains at zero despite the clearinghouse holding their tokens.

---

### Likelihood Explanation

The sequencer is a single centralized component. Sequencer downtime, operator error, or deliberate censorship of specific addresses are realistic scenarios. Slow mode is the protocol's stated mitigation for exactly these cases. Without on-chain executor incentives, the queue stalls precisely when it is most needed. A user who has been censored has no recourse other than paying unbounded gas costs themselves to drain the entire queue ahead of their transaction.

---

### Recommendation

Distribute a portion of the accumulated `slowModeFees` to the caller of `executeSlowModeTransaction()` as a gas rebate. Alternatively, allow a submitter to execute their own specific slow-mode transaction directly (bypassing FIFO) after the delay has elapsed, so they are not forced to pay for unrelated transactions. At minimum, document the dependency on the sequencer for queue execution so users understand the trust assumption.

---

### Proof of Concept

1. User calls `depositCollateral(subaccountName, productId, 1000e6)` — 1000 USDC is transferred to the clearinghouse; a `SlowModeTx` is enqueued at index `N` with `executableAt = block.timestamp + 3 days`.
2. The sequencer goes offline (or censors this user).
3. Three days pass; the slow-mode tx at index `N` is now executable.
4. No external actor calls `executeSlowModeTransaction()` because there is no reward — gas costs exceed any benefit.
5. The user's on-chain USDC balance in `SpotEngine` remains zero. Their 1000 USDC is held by the clearinghouse with no path to credit or recovery unless the user pays gas to execute every queued transaction from index `txUpTo` up to `N`. [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/Endpoint.sol (L144-148)
```text
        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
```

**File:** core/contracts/Endpoint.sol (L150-166)
```text
        SlowModeConfig memory _slowModeConfig = slowModeConfig;

        slowModeTxs[_slowModeConfig.txCount++] = SlowModeTx({
            executableAt: uint64(block.timestamp) + SLOW_MODE_TX_DELAY, // hardcoded to three days
            sender: sender,
            tx: abi.encodePacked(
                uint8(TransactionType.DepositCollateral),
                abi.encode(
                    DepositCollateral({
                        sender: subaccount,
                        productId: productId,
                        amount: amount
                    })
                )
            )
        });
        slowModeConfig = _slowModeConfig;
```

**File:** core/contracts/Endpoint.sol (L185-236)
```text
    function _executeSlowModeTransaction(
        SlowModeConfig memory _slowModeConfig,
        bool fromSequencer
    ) internal {
        require(
            _slowModeConfig.txUpTo < _slowModeConfig.txCount,
            ERR_NO_SLOW_MODE_TXS_REMAINING
        );
        SlowModeTx memory txn = slowModeTxs[_slowModeConfig.txUpTo];
        delete slowModeTxs[_slowModeConfig.txUpTo++];

        require(
            fromSequencer || (txn.executableAt <= block.timestamp),
            ERR_SLOW_TX_TOO_RECENT
        );

        if (block.chainid == 31337) {
            // for testing purposes, we don't fail silently when the chainId is hardhat's default.
            this.processSlowModeTransaction(txn.sender, txn.tx);
        } else {
            uint256 gasRemaining = gasleft();
            // solhint-disable-next-line no-empty-blocks
            try this.processSlowModeTransaction(txn.sender, txn.tx) {} catch {
                // we need to differentiate between a revert and an out of gas
                // the issue is that in evm every inner call only 63/64 of the
                // remaining gas in the outer frame is forwarded. as a result
                // the amount of gas left for execution is (63/64)**len(stack)
                // and you can get an out of gas while spending an arbitrarily
                // low amount of gas in the final frame. we use a heuristic
                // here that isn't perfect but covers our cases.
                // having gasleft() <= gasRemaining / 2 buys us 44 nested calls
                // before we miss out of gas errors; 1/2 ~= (63/64)**44
                // this is good enough for our purposes

                if (gasleft() <= 250000 || gasleft() <= gasRemaining / 2) {
                    // solhint-disable-next-line no-inline-assembly
                    assembly {
                        invalid()
                    }
                }

                // try return funds now removed
            }
        }
    }

    function executeSlowModeTransaction() external {
        SlowModeConfig memory _slowModeConfig = slowModeConfig;
        _executeSlowModeTransaction(_slowModeConfig, false);
        nSubmissions += 1;
        slowModeConfig = _slowModeConfig;
    }
```

**File:** core/contracts/Endpoint.sol (L257-261)
```text
        if (txType == TransactionType.ExecuteSlowMode) {
            SlowModeConfig memory _slowModeConfig = slowModeConfig;
            _executeSlowModeTransaction(_slowModeConfig, true);
            slowModeConfig = _slowModeConfig;
        } else {
```

**File:** core/contracts/EndpointTx.sol (L369-372)
```text
        } else {
            chargeSlowModeFee(_getQuote(), sender);
            slowModeFees += SLOW_MODE_FEE;
        }
```

**File:** core/contracts/EndpointStorage.sol (L55-55)
```text
    int128 internal slowModeFees;
```

**File:** core/contracts/common/Constants.sol (L23-23)
```text
int128 constant SLOW_MODE_FEE = 1000000; // $1
```

**File:** core/contracts/common/Constants.sol (L50-50)
```text
uint64 constant SLOW_MODE_TX_DELAY = 3 * 24 * 60 * 60; // 3 days
```
