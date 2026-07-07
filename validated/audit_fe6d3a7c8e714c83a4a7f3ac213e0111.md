### Title
Unsigned `oraclePriceX18` in `SignedBurnNlp` / `SignedMintNlp` Allows Arbitrary Price Injection via Slow-Mode Path — (`Clearinghouse.sol`, `EndpointTx.sol`, `Verifier.sol`)

---

### Summary

The `oraclePriceX18` field embedded in `SignedBurnNlp` and `SignedMintNlp` is **not included in the EIP-712 signature digest** that the user signs. In the slow-mode path, the user submits the full transaction bytes — including `oraclePriceX18` — directly on-chain with no on-chain oracle validation. This allows an unprivileged user to inject an arbitrary price at execution time, causing the protocol to compute an inflated `quoteAmount` for a `BurnNlp` (or a deflated price for `MintNlp`), draining quote tokens from NLP pool subaccounts.

---

### Finding Description

**Step 1 — The user's signature does not commit to `oraclePriceX18`.**

The `BurnNlp` inner struct that the user signs contains only `{sender, nlpAmount, nonce}`: [1](#0-0) 

The EIP-712 digest computed in `Verifier.computeDigest` for `BurnNlp` hashes only those three fields: [2](#0-1) 

`oraclePriceX18` and `nlpPoolRebalanceX18` live outside the signed struct in `SignedBurnNlp` and are never hashed into the digest. The same pattern applies to `SignedMintNlp`: [3](#0-2) 

**Step 2 — The unsigned `oraclePriceX18` is used directly to compute the settlement amount.**

In `Clearinghouse.burnNlp`, the quote tokens credited to the user are computed as: [4](#0-3) 

There is no on-chain check that `oraclePriceX18` is within any reasonable range, matches any stored oracle price, or is bounded in any way.

**Step 3 — The slow-mode path gives an unprivileged user full control over `oraclePriceX18`.**

`submitSlowModeTransactionImpl` stores the raw transaction bytes as submitted by the caller. `BurnNlp` is not in the admin-only list and is accepted from any user (after paying the slow-mode fee): [5](#0-4) 

When the slow-mode transaction is later executed, `processTransactionImpl` decodes `signedTx.oraclePriceX18` directly from the stored bytes and passes it to `clearinghouse.burnNlp` without any re-validation: [6](#0-5) 

The user who submitted the slow-mode transaction chose every byte of that payload, including `oraclePriceX18`.

**Step 4 — NLP pool accounting is corrupted.**

`_applyNlpRebalance` reduces NLP pool subaccount quote balances by the full (inflated) `quoteAmount`. There is no health check on NLP pool subaccounts inside `burnNlp`; only the burner's own maintenance health is checked: [7](#0-6) 

The NLP pool subaccount quote balance can be driven negative, while the attacker's quote balance is inflated by the same amount.

---

### Impact Explanation

An attacker burns a small `nlpAmount` of NLP tokens but sets `oraclePriceX18` to an astronomically large value. The protocol credits `nlpAmount × oraclePriceX18` in quote tokens to the attacker's subaccount and debits the same from NLP pool subaccounts. The attacker then calls `withdrawCollateral` to extract real ERC-20 tokens from the `WithdrawPool`. NLP pool subaccounts are left with deeply negative quote balances, corrupting the accounting for all NLP holders and potentially draining the protocol's quote token reserves.

The symmetric attack on `MintNlp` (deflated `oraclePriceX18`) lets the attacker receive a disproportionately large `nlpAmount` for a small `quoteAmount`, which can then be burned at the correct price to extract value.

---

### Likelihood Explanation

The slow-mode path is a censorship-resistance escape hatch explicitly designed to be accessible to any user without sequencer cooperation. The only barrier is the slow-mode fee and a 3-day delay. The attack requires no privileged access, no leaked keys, and no social engineering. Any user who can pay the slow-mode fee and wait 3 days can execute it.

---

### Recommendation

Include `oraclePriceX18` in the EIP-712 digest for both `BurnNlp` and `MintNlp`, so the user's signature commits to the price at which their NLP will be settled. Additionally, add an on-chain sanity bound (e.g., require `oraclePriceX18` to be within a configurable percentage of the last sequencer-submitted price stored in `priceX18[NLP_PRODUCT_ID]`) before executing the settlement calculation.

---

### Proof of Concept

1. Attacker holds 1 NLP token (`nlpAmount = 1e18`).
2. Attacker calls `submitSlowModeTransaction` with a `BurnNlp` payload where:
   - `tx = {sender: attacker, nlpAmount: 1e18, nonce: N}` — signed normally.
   - `oraclePriceX18 = 1e36` — injected outside the signed struct, not validated.
   - `nlpPoolRebalanceX18 = [-1e54]` — sums to `-quoteAmount`, passes `_validateNlpRebalance`.
3. After 3 days, anyone calls `executeSlowModeTransaction`.
4. `Clearinghouse.burnNlp` computes `quoteAmount = 1e18 * 1e36 = 1e54`.
5. Attacker's quote balance increases by `1e54 - burnFee`; NLP pool quote balance decreases by `1e54`.
6. Attacker calls `withdrawCollateral` to drain the protocol's actual ERC-20 quote token balance. [8](#0-7) [6](#0-5) [2](#0-1)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L125-136)
```text
    struct BurnNlp {
        bytes32 sender;
        uint128 nlpAmount;
        uint64 nonce;
    }

    struct SignedBurnNlp {
        BurnNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }
```

**File:** core/contracts/Verifier.sol (L373-385)
```text
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
            IEndpoint.SignedMintNlp memory signedTx = abi.decode(
                transactionBody,
                (IEndpoint.SignedMintNlp)
            );
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(MINT_NLP_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.quoteAmount,
                    signedTx.tx.nonce
                )
            );
```

**File:** core/contracts/Verifier.sol (L386-398)
```text
        } else if (txType == IEndpoint.TransactionType.BurnNlp) {
            IEndpoint.SignedBurnNlp memory signedTx = abi.decode(
                transactionBody,
                (IEndpoint.SignedBurnNlp)
            );
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(BURN_NLP_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.nlpAmount,
                    signedTx.tx.nonce
                )
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
