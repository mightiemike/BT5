### Title
Fee-on-Transfer Token Accounting Inflation in `depositCollateral` Allows Balance Overstatement — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary
`Clearinghouse.depositCollateral` credits a user's spot balance using the caller-supplied `txn.amount` rather than the actual token amount received by the contract. When a fee-on-transfer ERC20 is used as collateral, the Clearinghouse receives fewer tokens than it records, inflating every depositor's internal balance and creating a cumulative insolvency gap.

---

### Finding Description

The deposit flow in Nado is:

1. A user approves the `Endpoint` and calls `depositCollateralWithReferral(subaccount, productId, amount, referral)`.
2. The `Endpoint` executes `token.safeTransferFrom(user, clearinghouse, amount)` — the actual on-chain token movement.
3. The `Endpoint` then calls `Clearinghouse.depositCollateral(txn)` where `txn.amount` equals the user-specified `amount`.
4. Inside `depositCollateral`, the contract scales and credits the user's balance unconditionally from `txn.amount`:

```solidity
// Clearinghouse.sol lines 204-207
int256 multiplier = int256(10**(MAX_DECIMALS - decimals));
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
```

`txn.amount` is the amount the user *declared*, not the amount the contract *received*. For a fee-on-transfer token with fee rate `f`, the Clearinghouse receives `amount × (1 - f)` but credits `amount`. The delta `amount × f` is phantom balance — it exists in the accounting ledger but has no backing token.

The same pattern repeats in `depositInsurance`:

```solidity
// Clearinghouse.sol lines 265-266
int128 amount = int128(txn.amount) * int128(multiplier);
insurance += amount;
```

The insurance fund is similarly overstated.

A secondary instance exists in `BaseWithdrawPool.submitFastWithdrawal` at line 108, where a third-party LP pays the fast-withdrawal fee via `safeTransferFrom`. If the token carries a transfer fee, the pool records `fees[productId] += fee` (line 111) but physically receives `fee - transferFee`, understating the pool's real balance.

---

### Impact Explanation

Every deposit with a fee-on-transfer token inflates the depositor's `SpotEngine` balance by `fee × multiplier` units. Because `withdrawCollateral` debits the same internal balance and then transfers the full nominal amount out of the pool, the protocol's token holdings fall below the sum of all recorded balances. The shortfall compounds with each deposit. Eventually, the pool cannot satisfy all withdrawal requests; the last withdrawers suffer a direct loss of principal proportional to the total accumulated fee delta. This is a solvency/accounting corruption with a concrete, measurable asset delta per deposit.

---

### Likelihood Explanation

Likelihood is **low**. It requires a listed collateral asset to implement a transfer fee, either at launch or via an upgradeable token contract that activates fees post-listing. The external report's referenced protocol (Evoq/Morpho) acknowledged the same class and accepted the risk on the same grounds. Nado's risk is identical in structure: if any listed spot token activates a fee, every subsequent deposit silently inflates balances until the protocol is insolvent.

---

### Recommendation

After `safeTransferFrom` completes in the `Endpoint`, measure the actual balance delta of the `Clearinghouse` and pass that value — not the user-supplied `amount` — into `depositCollateral`. Concretely:

```solidity
uint256 before = token.balanceOf(address(clearinghouse));
token.safeTransferFrom(msg.sender, clearinghouse, amount);
uint256 actualReceived = token.balanceOf(address(clearinghouse)) - before;
// pass actualReceived (scaled to uint128) into the DepositCollateral txn
```

Apply the same pattern to `depositInsurance` and to the LP fee `safeTransferFrom` in `BaseWithdrawPool.submitFastWithdrawal`.

---

### Proof of Concept

Assume a fee-on-transfer USDC-equivalent token with a 1 % transfer fee is listed as a spot collateral.

1. **Alice** calls `depositCollateralWithReferral(aliceSubaccount, productId, 1000e6, "")`.
2. `Endpoint` executes `token.safeTransferFrom(Alice, Clearinghouse, 1000e6)`.
   - Clearinghouse receives **990e6** (1 % fee deducted).
3. `Endpoint` calls `Clearinghouse.depositCollateral` with `txn.amount = 1000e6`.
4. `amountRealized = 1000e6 × 10^(18-6) = 1000e18` is credited to Alice's spot balance.
   - Actual backing: **990e18** equivalent. Phantom credit: **10e18**.
5. Alice calls `withdrawCollateral` for `1000e6`.
   - `spotEngine.updateBalance` debits `1000e18` — valid per ledger.
   - `token.safeTransfer(withdrawPool, 1000e6)` — pool sends **1000e6** but only ever held **990e6** from Alice's deposit.
6. The pool is now short **10e6** tokens. If Bob deposited 1000e6 earlier (also fee-bearing), the pool is short **20e6** in aggregate. The last depositor to withdraw cannot be made whole.

**Corrupted state**: `SpotEngine` balance for Alice overstated by `fee × multiplier`; Clearinghouse token holdings permanently below the sum of all recorded spot balances. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** core/contracts/Clearinghouse.sol (L252-267)
```text
    function depositInsurance(bytes calldata transaction)
        external
        virtual
        onlyEndpoint
    {
        IEndpoint.DepositInsurance memory txn = abi.decode(
            transaction[1:],
            (IEndpoint.DepositInsurance)
        );
        require(txn.amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int256 multiplier = int256(
            10**(MAX_DECIMALS - _decimals(QUOTE_PRODUCT_ID))
        );
        int128 amount = int128(txn.amount) * int128(multiplier);
        insurance += amount;
    }
```

**File:** core/contracts/Clearinghouse.sol (L377-385)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount,
        uint64 idx
    ) internal virtual {
        token.safeTransfer(withdrawPool, uint256(amount));
        BaseWithdrawPool(withdrawPool).submitWithdrawal(token, to, amount, idx);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L104-113)
```text
        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
```
