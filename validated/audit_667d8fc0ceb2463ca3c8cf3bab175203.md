### Title
`transferQuote` Missing Decimal Multiplier Corrupts WAD-Denominated Balance Accounting — (`core/contracts/Clearinghouse.sol`)

---

### Summary

`Clearinghouse.transferQuote` passes `txn.amount` (a raw token amount) directly to `spotEngine.updateBalance` without applying the `10^(MAX_DECIMALS - decimals)` scaling factor that every other collateral-touching function in the same contract applies. For a quote token with fewer than 18 decimals (e.g., USDC at 6 decimals), the balance delta written to the spot engine is `10^12` times smaller than intended, making the transfer effectively a no-op and trivially passing the post-transfer health check.

---

### Finding Description

Every function in `Clearinghouse.sol` that converts a raw token amount into the protocol's internal WAD representation applies the multiplier:

```solidity
int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
int128 amountRealized = int128(amount) * int128(multiplier);
spotEngine.updateBalance(productId, sender, amountRealized);
```

This is done consistently in `depositCollateral`, `withdrawCollateral`, `depositInsurance`, and `withdrawInsurance`. [1](#0-0) [2](#0-1) [3](#0-2) 

`transferQuote`, however, casts `txn.amount` directly to `int128` and passes it to `updateBalance` with no scaling:

```solidity
int128 toTransfer = int128(txn.amount);          // raw token units, NOT WAD
...
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender,    -toTransfer);
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient,  toTransfer);
require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
``` [4](#0-3) 

`TransferQuote.amount` is `uint128`, the same type used for raw token amounts in every other transaction struct (`DepositCollateral`, `WithdrawCollateral`, `WithdrawInsurance`). [5](#0-4) 

For USDC (6 decimals), the missing multiplier is `10^12`. A transfer of 1,000 USDC (`1_000e6` raw) writes only `1_000e6` WAD units to both balances instead of `1_000e18` WAD units — a factor of `10^12` error.

---

### Impact Explanation

**Corrupted balance delta (concrete):**
- Intended sender deduction: `1_000e18` WAD (≈ 1,000 USDC)
- Actual sender deduction: `1_000e6` WAD (≈ 0.000001 USDC)
- Corrupted delta retained by sender: `999_999_000_000_000_000_000` WAD ≈ 1,000 USDC

**Health check bypass:** Because the sender's WAD balance barely decreases, `_isAboveInitial(txn.sender)` passes trivially regardless of the nominal transfer size. A user can submit a `TransferQuote` for their entire quote balance and the health check will not reject it. [6](#0-5) 

**Accounting desynchronisation:** The sum of all subaccount WAD balances in the spot engine diverges from the actual token holdings of the Clearinghouse. Downstream operations that rely on those balances (health calculations, liquidation thresholds, NLP rebalancing) operate on incorrect state.

**Isolated subaccount collateral flow:** The primary use of `transferQuote` is moving quote collateral between a regular subaccount and its isolated subaccounts. With the bug active, neither direction of transfer produces a meaningful balance change, so isolated subaccounts cannot be properly funded or drained through this path. [7](#0-6) 

---

### Likelihood Explanation

- USDC (6 decimals) is the designated quote token on Ink Chain — the multiplier mismatch is `10^12` on every deployment.
- Any user can craft and sign a `TransferQuote` transaction; the sequencer submits it via `Endpoint → clearinghouse.transferQuote`. No privileged role is required.
- The code path is unconditional: there is no branch that applies the multiplier for certain token types.

---

### Recommendation

Apply the same decimal-normalisation pattern used by every other collateral function:

```solidity
function transferQuote(IEndpoint.TransferQuote calldata txn)
    external virtual onlyEndpoint
{
    require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
    int256 multiplier = int256(
        10**(MAX_DECIMALS - _decimals(QUOTE_PRODUCT_ID))
    );
    int128 toTransfer = int128(txn.amount) * int128(multiplier);
    ...
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender,    -toTransfer);
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient,  toTransfer);
    require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
}
```

Add test coverage for `transferQuote` with a 6-decimal quote token, asserting that the WAD balance delta equals `amount × 10^(18−decimals)`.

---

### Proof of Concept

1. Quote token is USDC (6 decimals); `MAX_DECIMALS = 18`; multiplier = `10^12`.
2. Alice deposits 1,000 USDC via `depositCollateral` → her WAD balance = `1_000e18`.
3. Alice signs a `TransferQuote` for `amount = 1_000e6` (raw USDC) to her isolated subaccount.
4. Sequencer submits the transaction; `Clearinghouse.transferQuote` executes:
   - `toTransfer = int128(1_000e6)` — no scaling applied.
   - `spotEngine.updateBalance(QUOTE_PRODUCT_ID, alice_regular, -1_000e6)` → Alice's balance = `1_000e18 − 1_000e6 ≈ 1_000e18`.
   - `spotEngine.updateBalance(QUOTE_PRODUCT_ID, alice_isolated, +1_000e6)` → isolated balance = `1_000e6` WAD ≈ 0.000001 USDC.
5. `_isAboveInitial(alice_regular)` passes trivially (balance barely changed).
6. Alice's regular subaccount retains ≈ 1,000 USDC of WAD balance; her isolated subaccount holds ≈ 0.000001 USDC — the intended 1,000 USDC transfer never occurred in the accounting layer.
7. Alice can now withdraw her full 1,000 USDC from the regular subaccount while the isolated subaccount's balance is near-zero, leaving the protocol's internal accounting permanently desynchronised from actual token holdings. [8](#0-7) [9](#0-8)

### Citations

**File:** core/contracts/Clearinghouse.sol (L193-209)
```text
    function depositCollateral(IEndpoint.DepositCollateral calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        uint8 decimals = _decimals(txn.productId);

        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
        int128 amountRealized = int128(txn.amount) * int128(multiplier);

        spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
        emit ModifyCollateral(amountRealized, txn.sender, txn.productId);
    }
```

**File:** core/contracts/Clearinghouse.sol (L211-250)
```text
    function transferQuote(IEndpoint.TransferQuote calldata txn)
        external
        virtual
        onlyEndpoint
    {
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 toTransfer = int128(txn.amount);
        ISpotEngine spotEngine = _spotEngine();

        // require the sender address to be the same as the recipient address
        // otherwise linked signers can transfer out
        require(
            bytes20(txn.sender) == bytes20(txn.recipient),
            ERR_UNAUTHORIZED
        );
        address offchainExchange = IEndpoint(getEndpoint())
            .getOffchainExchange();
        if (RiskHelper.isIsolatedSubaccount(txn.sender)) {
            // isolated subaccounts can only transfer quote back to parent
            require(
                IOffchainExchange(offchainExchange).getParentSubaccount(
                    txn.sender
                ) == txn.recipient,
                ERR_UNAUTHORIZED
            );
        } else if (RiskHelper.isIsolatedSubaccount(txn.recipient)) {
            // regular subaccounts can transfer quote to active isolated subaccounts
            require(
                IOffchainExchange(offchainExchange).isIsolatedSubaccountActive(
                    txn.sender,
                    txn.recipient
                ),
                ERR_UNAUTHORIZED
            );
        }

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -toTransfer);
        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient, toTransfer);
        require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
    }
```

**File:** core/contracts/Clearinghouse.sol (L279-284)
```text
        int256 multiplier = int256(
            10**(MAX_DECIMALS - _decimals(QUOTE_PRODUCT_ID))
        );
        int128 amount = int128(txn.amount) * int128(multiplier);
        require(amount <= insurance, ERR_NO_INSURANCE);
        insurance -= amount;
```

**File:** core/contracts/Clearinghouse.sol (L409-412)
```text

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
```

**File:** core/contracts/interfaces/IEndpoint.sol (L309-314)
```text
    struct TransferQuote {
        bytes32 sender;
        bytes32 recipient;
        uint128 amount;
        uint64 nonce;
    }
```

**File:** core/contracts/EndpointTx.sol (L593-614)
```text
        } else if (txType == IEndpoint.TransactionType.TransferQuote) {
            IEndpoint.SignedTransferQuote memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedTransferQuote)
            );
            _recordSubaccount(signedTx.tx.recipient);
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            if (
                RiskHelper.isIsolatedSubaccount(signedTx.tx.recipient) ||
                RiskHelper.isIsolatedSubaccount(signedTx.tx.sender)
            ) {
                chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE / 10);
            } else {
                chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            }
            clearinghouse.transferQuote(signedTx.tx);
```
