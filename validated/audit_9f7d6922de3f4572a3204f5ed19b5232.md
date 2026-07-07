### Title
Fee-on-Transfer Token Accounting Inflation in `depositCollateralWithReferral` Overcredits Subaccount Balances — (File: `core/contracts/EndpointStorage.sol`, `core/contracts/Endpoint.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

`depositCollateralWithReferral` in `Endpoint.sol` uses the caller-supplied `amount` for both the token pull from the user and the token push to the Clearinghouse, and then encodes that same `amount` into the slow-mode transaction that later credits the subaccount. For a fee-on-transfer collateral token, the Clearinghouse receives fewer tokens than `amount`, but the subaccount is credited the full `amount`. This inflates subaccount balances beyond the protocol's actual token holdings, creating a solvency deficit that grows with every such deposit.

---

### Finding Description

The deposit path is:

**Step 1 — `Endpoint.depositCollateralWithReferral`** calls `handleDepositTransfer` with the raw caller-supplied `amount`, then immediately encodes that same `amount` into a `SlowModeTx`. [1](#0-0) 

**Step 2 — `EndpointStorage.handleDepositTransfer`** performs two sequential transfers, both using the original `amount`:

```
safeTransferFrom(token, from, amount)          // Endpoint receives amount − fee₁
safeTransferTo(token, address(clearinghouse), amount)  // Clearinghouse receives amount − fee₂
``` [2](#0-1) 

After `safeTransferFrom`, the Endpoint holds only `amount − fee₁`. The subsequent `safeTransferTo` attempts to forward the full `amount`. If the Endpoint carries a pre-existing token balance of at least `fee₁` (accumulated from slow-mode fees charged in the same token via `chargeSlowModeFee`, or from any other source), the forward succeeds — but the Clearinghouse receives only `amount − fee₂` due to the second transfer's fee.

**Step 3 — `Clearinghouse.depositCollateral`** (executed when the slow-mode tx is processed) credits `txn.amount` — the original, unmodified `amount` — to the subaccount:

```solidity
int128 amountRealized = int128(txn.amount) * int128(multiplier);
spotEngine.updateBalance(txn.productId, txn.sender, amountRealized);
``` [3](#0-2) 

At no point is the actual received balance measured (e.g., via a before/after `balanceOf` check). The protocol never reconciles `txn.amount` against what the Clearinghouse actually holds.

The same structural flaw exists on the withdrawal path: `Clearinghouse.handleWithdrawTransfer` forwards the full `amount` to the `WithdrawPool`, which then attempts to forward the same `amount` to the recipient — but the WithdrawPool received only `amount − fee` from the Clearinghouse. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

Each successful deposit of a fee-on-transfer collateral token inflates the depositor's subaccount balance by `fee₂` (the fee on the Endpoint→Clearinghouse leg) relative to what the Clearinghouse actually holds. Across many deposits, the cumulative gap between the sum of all subaccount balances and the Clearinghouse's real token holdings grows unboundedly. Any user whose subaccount was inflated can later withdraw the phantom balance, draining tokens that belong to other depositors. This is a direct solvency/accounting corruption with concrete asset loss for honest users.

---

### Likelihood Explanation

The precondition is that (a) a fee-on-transfer token is listed as a collateral product, and (b) the Endpoint holds a non-zero balance of that token at deposit time. Condition (b) is satisfied whenever `chargeSlowModeFee` has been called for that token (slow-mode fees accumulate in the Endpoint), or whenever any other code path leaves a residual balance. Both conditions are realistic for any protocol that lists a deflationary or rebasing token as collateral. The entry point (`depositCollateralWithReferral`) is public and requires no privilege. [6](#0-5) 

---

### Recommendation

Replace the fixed-`amount` two-transfer pattern in `handleDepositTransfer` with a balance-before/after measurement to determine the actual amount received by the Clearinghouse, and use that measured value — not the caller-supplied `amount` — when constructing the `DepositCollateral` slow-mode transaction. The same fix should be applied to the withdrawal path: measure what the WithdrawPool actually receives and credit/debit only that amount.

```solidity
function handleDepositTransfer(IERC20Base token, address from, uint256 amount)
    internal returns (uint256 actualReceived)
{
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(clearinghouse));
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
    actualReceived = token.balanceOf(address(clearinghouse)) - before;
}
```

Then pass `actualReceived` (cast to `uint128`) into the `DepositCollateral` struct instead of `amount`.

---

### Proof of Concept

1. Token `T` has a 1% fee-on-transfer (recipient receives 99% of the specified amount).
2. The Endpoint holds ≥ 10 T from prior slow-mode fee accumulation in token `T`.
3. Attacker calls `depositCollateralWithReferral(subaccount, productId, 1000, ref)`.
4. `handleDepositTransfer` pulls 1000 T from attacker; Endpoint receives 990 T (fee₁ = 10).
5. `handleDepositTransfer` forwards 1000 T to Clearinghouse (drawing 10 T from Endpoint's pre-existing balance); Clearinghouse receives 990 T (fee₂ = 10).
6. `SlowModeTx` is queued encoding `amount = 1000`.
7. When the sequencer processes the slow-mode tx, `Clearinghouse.depositCollateral` credits `1000 × multiplier` to the subaccount.
8. Clearinghouse holds 990 T but owes 1000 T to the subaccount — a 10 T phantom balance.
9. Attacker withdraws 1000 T worth of collateral, receiving 10 T that belonged to other depositors.
10. Repeating step 3–9 drains the Endpoint's pre-existing balance and progressively deepens the Clearinghouse insolvency. [7](#0-6) [2](#0-1) [8](#0-7)

### Citations

**File:** core/contracts/Endpoint.sol (L123-167)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);

        if (!isValidDepositAmount(subaccount, productId, amount)) {
            // we cannot revert here, otherwise direct deposit could be blocked when there are
            // multiple assets awaiting credit but one of them is below the minimum deposit amount.
            // we can just skip the deposit and continue with the next asset.
            return;
        }

        handleDepositTransfer(
            IERC20Base(spotEngine.getToken(productId)),
            msg.sender,
            uint256(amount)
        );
        // copy from submitSlowModeTransaction
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
    }
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

**File:** core/contracts/EndpointStorage.sol (L111-119)
```text
    function handleDepositTransfer(
        IERC20Base token,
        address from,
        uint256 amount
    ) internal {
        require(address(token) != address(0), ERR_INVALID_PRODUCT);
        safeTransferFrom(token, from, amount);
        safeTransferTo(token, address(clearinghouse), amount);
    }
```

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

**File:** core/contracts/BaseWithdrawPool.sol (L184-190)
```text
    function handleWithdrawTransfer(
        IERC20Base token,
        address to,
        uint128 amount
    ) internal virtual {
        token.safeTransfer(to, uint256(amount));
    }
```
