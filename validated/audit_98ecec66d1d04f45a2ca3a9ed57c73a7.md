### Title
Missing Decimal Multiplier in `transferQuote` Corrupts Internal Quote Balance Accounting - (File: `core/contracts/Clearinghouse.sol`)

### Summary
`transferQuote` in `Clearinghouse.sol` updates internal spot-engine balances using the raw `txn.amount` value without applying the `10**(MAX_DECIMALS - decimals)` scaling multiplier that every other balance-modifying function in the same contract applies. Because the protocol stores all balances in an 18-decimal (X18) fixed-point representation while the quote token (USDC) has 6 decimals, the internal balance delta is 10¹² times smaller than the user intended.

### Finding Description
Every function in `Clearinghouse.sol` that converts a user-supplied token amount into an internal X18 balance applies the same scaling pattern:

```solidity
int256 multiplier = int256(10**(MAX_DECIMALS - decimals)); // e.g. 10^12 for USDC
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(..., amountRealized);
```

`depositCollateral` applies this multiplier: [1](#0-0) 

`withdrawCollateral` applies this multiplier: [2](#0-1) 

`depositInsurance` and `withdrawInsurance` both apply this multiplier: [3](#0-2) 

`transferQuote`, however, casts `txn.amount` directly to `int128` with no multiplier and passes it straight to `updateBalance`:

```solidity
int128 toTransfer = int128(txn.amount);   // ← no multiplier
...
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender,    -toTransfer);
spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient,  toTransfer);
``` [4](#0-3) 

`MAX_DECIMALS` is 18 and the quote token is USDC (6 decimals), so the missing multiplier is `10**12`. [5](#0-4) 

### Impact Explanation
A user who signs a `TransferQuote` transaction specifying `amount = 1_000_000` (1 USDC in native units) will have their internal X18 balance adjusted by only `1_000_000` instead of `1_000_000 * 10^12 = 10^18`. The actual credit/debit is 10¹² times smaller than intended. This corrupts the internal accounting for both the sender and recipient subaccounts: the sender retains far more internal balance than it should, and the recipient receives far less. Because the health check `_isAboveInitial(txn.sender)` operates on the (barely-changed) internal balance, it passes trivially, masking the accounting error. Any downstream logic that relies on the quote balance of either subaccount — margin calculations, isolated-subaccount funding, NLP pool accounting — will operate on a corrupted state.

### Likelihood Explanation
`TransferQuote` is a user-signed fast-mode transaction. Any user with a registered subaccount can craft and submit one through the sequencer. The sequencer passes the signed payload to the on-chain contract unchanged; the scaling error is unconditional in the contract logic. The constraint `bytes20(txn.sender) == bytes20(txn.recipient)` limits the transfer to subaccounts of the same address, but the accounting corruption affects both the regular and isolated subaccounts of that address, which is the primary use-case for this transaction type.

### Recommendation
Apply the same decimal-scaling pattern used in `depositCollateral` and `withdrawCollateral`:

```solidity
function transferQuote(IEndpoint.TransferQuote calldata txn) external virtual onlyEndpoint {
    require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
    uint8 decimals = _decimals(QUOTE_PRODUCT_ID);
    require(decimals <= MAX_DECIMALS);
    int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
    int128 toTransfer = int128(txn.amount) * int128(multiplier);
    ...
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender,    -toTransfer);
    spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.recipient,  toTransfer);
    require(_isAboveInitial(txn.sender), ERR_SUBACCT_HEALTH);
}
```

### Proof of Concept
1. User holds 1,000 USDC deposited via `depositCollateral`. Internal balance = `1_000 * 10^6 * 10^12 = 10^21` (X18 units). ✓ correct.
2. User signs a `TransferQuote` with `amount = 500_000_000` (500 USDC in native units) to move funds to their isolated subaccount.
3. On-chain: `toTransfer = int128(500_000_000)` — no multiplier applied.
4. Sender's internal balance decreases by `500_000_000` X18 units = `5 × 10^-10` USDC. Recipient's balance increases by the same negligible amount.
5. Health check passes (sender barely lost anything). Both subaccounts now carry corrupted balances. The isolated subaccount is effectively unfunded despite the user's intent. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** core/contracts/Clearinghouse.sol (L193-208)
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
```

**File:** core/contracts/Clearinghouse.sol (L211-249)
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
```

**File:** core/contracts/Clearinghouse.sol (L261-266)
```text
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int256 multiplier = int256(
            10**(MAX_DECIMALS - _decimals(QUOTE_PRODUCT_ID))
        );
        int128 amount = int128(txn.amount) * int128(multiplier);
        insurance += amount;
```

**File:** core/contracts/Clearinghouse.sol (L410-412)
```text
        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
```

**File:** core/contracts/common/Constants.sol (L17-19)
```text
int128 constant ONE = 10**18;

uint8 constant MAX_DECIMALS = 18;
```
