### Title
Sequencer-Injected `oraclePriceX18` Excluded from EIP-712 Digest in `SignedBurnNlp`/`SignedMintNlp` Enables Stale-Price NLP Redemption via Slow Mode — (`File: core/contracts/Verifier.sol`, `core/contracts/EndpointTx.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

The `oraclePriceX18` field embedded in `SignedBurnNlp` and `SignedMintNlp` is structurally outside the user-signed `tx` struct and is **not included in the EIP-712 digest** computed by `Verifier.sol`. Because `BurnNlp`/`MintNlp` transactions are not blocked from the slow-mode submission path, an unprivileged user can craft a `BurnNlp` slow-mode transaction with an arbitrarily inflated `oraclePriceX18`, submit it with a valid signature over only `{sender, nlpAmount, nonce}`, and — after the 3-day delay — have it executed with the user-controlled price. The inflated price is written directly into the engine's NLP risk store and used to compute the quote payout, allowing the attacker to drain quote from the NLP pool at a price far above the real NLP NAV.

---

### Finding Description

**Step 1 — `oraclePriceX18` is outside the signed struct.**

`SignedBurnNlp` is defined as:

```solidity
struct SignedBurnNlp {
    BurnNlp tx;          // { sender, nlpAmount, nonce }
    bytes signature;
    int128 oraclePriceX18;       // ← outside tx, not signed
    int128[] nlpPoolRebalanceX18;
}
``` [1](#0-0) 

**Step 2 — The EIP-712 digest for `BurnNlp` covers only `{sender, nlpAmount, nonce}`.**

```solidity
digest = keccak256(
    abi.encode(
        keccak256(bytes(BURN_NLP_SIGNATURE)),
        signedTx.tx.sender,
        signedTx.tx.nlpAmount,
        signedTx.tx.nonce          // oraclePriceX18 absent
    )
);
``` [2](#0-1) 

The same omission applies to `MintNlp`: [3](#0-2) 

**Step 3 — `BurnNlp` is not blocked from the slow-mode submission path.**

`submitSlowModeTransactionImpl` explicitly reverts only `DepositCollateral` and restricts a fixed set of admin-only types. `BurnNlp` falls into the `else` branch: pay the slow-mode fee and enqueue. [4](#0-3) 

**Step 4 — When executed, `oraclePriceX18` is used verbatim to price the NLP redemption.**

In `EndpointTx.processTransactionImpl` (reached via `processSlowModeTransaction`):

```solidity
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
clearinghouse.burnNlp(signedTx.tx, signedTx.oraclePriceX18, nlpPools, ...);
``` [5](#0-4) 

Inside `Clearinghouse.burnNlp`:

```solidity
spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);   // persists stale price
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);        // payout computed at attacker price
``` [6](#0-5) 

The post-burn health check uses `MAINTENANCE` health, which itself reads `risk.priceX18` — the value just overwritten with the attacker's inflated price — so the check does not catch the over-payment. [7](#0-6) 

---

### Impact Explanation

An attacker who holds any amount of NLP tokens can submit a `BurnNlp` slow-mode transaction with `oraclePriceX18` set to an arbitrarily large value. After the 3-day delay, the transaction executes and the attacker receives `nlpAmount × inflatedPrice` in quote tokens, far exceeding the real NAV of the NLP they burned. The excess quote is drawn from the NLP pool's quote balances, directly draining liquidity providers. The inflated price is also persisted into the engine's risk store, corrupting subsequent health checks for all subaccounts that hold NLP as collateral until the next legitimate `MintNlp`/`BurnNlp` corrects it.

---

### Likelihood Explanation

Any holder of NLP tokens can execute this attack without any privileged access. The only prerequisite is owning NLP and waiting 3 days for the slow-mode delay. The slow-mode path is a documented, publicly accessible entry point (`submitSlowModeTransaction` is `external`). The attacker's signature is trivially valid because it covers only `{sender, nlpAmount, nonce}`, which are all attacker-controlled. No oracle manipulation, sequencer compromise, or governance capture is required. [8](#0-7) 

---

### Recommendation

Include `oraclePriceX18` (and `nlpPoolRebalanceX18`) in the EIP-712 digest for both `MintNlp` and `BurnNlp`:

```solidity
// Verifier.sol — BurnNlp digest
digest = keccak256(
    abi.encode(
        keccak256(bytes(BURN_NLP_SIGNATURE)),
        signedTx.tx.sender,
        signedTx.tx.nlpAmount,
        signedTx.tx.nonce,
        signedTx.oraclePriceX18,          // add
        keccak256(abi.encodePacked(signedTx.nlpPoolRebalanceX18)) // add
    )
);
```

Alternatively, if `oraclePriceX18` is intentionally sequencer-supplied (not user-signed), explicitly block `MintNlp` and `BurnNlp` from the slow-mode submission path in `submitSlowModeTransactionImpl`, the same way `DepositCollateral` is blocked.

---

### Proof of Concept

1. Attacker holds `N` NLP tokens.
2. Attacker constructs a `BurnNlp` transaction:
   - `tx = { sender: attacker_subaccount, nlpAmount: N, nonce: current_nonce }`
   - Signs only the `BurnNlp` struct (valid EIP-712 signature over `{sender, nlpAmount, nonce}`)
   - Sets `oraclePriceX18 = 1000e18` (1000× real NLP price)
   - Sets `nlpPoolRebalanceX18` to distribute the inflated quote payout
3. Calls `submitSlowModeTransaction(encodedBurnNlpTx)` — pays slow-mode fee, transaction is queued.
4. After `SLOW_MODE_TX_DELAY` (3 days), calls `executeSlowModeTransaction()`.
5. `processSlowModeTransaction` → `processSlowModeTransactionImpl` → `processTransactionImpl` executes the `BurnNlp`.
6. `validateSignedTx` passes (signature is valid for `{sender, nlpAmount, nonce}`).
7. `spotEngine.updatePrice(NLP_PRODUCT_ID, 1000e18)` persists the inflated price.
8. `quoteAmount = N × 1000e18` — attacker receives 1000× the real value of their NLP.
9. Maintenance health check passes because it reads the just-inflated `priceX18`.
10. Attacker withdraws the excess quote, draining the NLP pool. [9](#0-8) [10](#0-9)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L131-136)
```text
    struct SignedBurnNlp {
        BurnNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }
```

**File:** core/contracts/Verifier.sol (L378-385)
```text
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(MINT_NLP_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.quoteAmount,
                    signedTx.tx.nonce
                )
            );
```

**File:** core/contracts/Verifier.sol (L391-398)
```text
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(BURN_NLP_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.nlpAmount,
                    signedTx.tx.nonce
                )
            );
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

**File:** core/contracts/EndpointTx.sol (L554-573)
```text
        } else if (txType == IEndpoint.TransactionType.BurnNlp) {
            IEndpoint.SignedBurnNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedBurnNlp)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.burnNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```

**File:** core/contracts/Clearinghouse.sol (L485-530)
```text
    function burnNlp(
        IEndpoint.BurnNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.nlpAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 nlpAmount = int128(txn.nlpAmount);
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, -quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] <= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
        }

        require(
            spotEngine.getBalance(NLP_PRODUCT_ID, txn.sender).amount >= 0,
            ERR_SUBACCT_HEALTH
        );
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/Endpoint.sol (L173-183)
```text
    function submitSlowModeTransaction(bytes calldata transaction)
        external
        virtual
    {
        _delegatecallEndpointTx(
            abi.encodeWithSelector(
                EndpointTx.submitSlowModeTransactionImpl.selector,
                transaction
            )
        );
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
