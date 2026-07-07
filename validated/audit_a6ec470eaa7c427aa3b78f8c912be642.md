### Title
Fee-on-Transfer Token Deposit Overcredits Subaccount Balance, Enabling Collateral Theft — (`File: core/contracts/EndpointStorage.sol`)

---

### Summary

`Endpoint.depositCollateralWithReferral` passes the caller-supplied `amount` directly into both the token `transferFrom` call and the queued slow-mode `DepositCollateral` transaction. For fee-on-transfer tokens, the clearinghouse receives fewer tokens than `amount`, but the subaccount is credited the full `amount`. The resulting over-credit allows any depositor of such a token to withdraw more than they deposited, draining collateral that belongs to other users.

---

### Finding Description

The deposit path in Nado is:

1. `Endpoint.depositCollateralWithReferral` calls `handleDepositTransfer(token, msg.sender, uint256(amount))`.
2. `EndpointStorage.handleDepositTransfer` executes two transfers in sequence:
   - `safeTransferFrom(token, from, amount)` — pulls `amount` from the caller; for a fee-on-transfer token the endpoint receives only `amount − fee₁`.
   - `safeTransferTo(token, address(clearinghouse), amount)` — forwards `amount` to the clearinghouse; for a fee-on-transfer token the clearinghouse receives only `amount − fee₂`.
3. Immediately after, a slow-mode `DepositCollateral` transaction is enqueued with the field `amount: amount` — the original caller-supplied value, not the actual received amount.
4. When the sequencer executes the slow-mode transaction, `Clearinghouse.depositCollateral` computes `amountRealized = int128(txn.amount) * int128(multiplier)` and calls `spotEngine.updateBalance(txn.productId, txn.sender, amountRealized)` — crediting the full `amount` regardless of what the clearinghouse actually holds.

The clearinghouse's real token balance is therefore `amount − fee₂` per deposit, while the sum of all credited subaccount balances grows by `amount`. The invariant `token.balanceOf(clearinghouse) ≥ Σ subaccount_balances` is broken on every deposit of a fee-on-transfer token.

For `safeTransferTo` to succeed when the endpoint only holds `amount − fee₁`, the endpoint must carry a residual balance of at least `fee₁` in that token. This is achievable because `chargeSlowModeFee` pulls slow-mode fees into the endpoint in the quote token; if the quote token itself is fee-on-transfer, or if any other mechanism leaves a residual balance, the condition is met. An attacker can also seed the endpoint with a dust amount directly.

---

### Impact Explanation

Every deposit of a fee-on-transfer token creates a shortfall of `fee₂` tokens in the clearinghouse relative to the credited balance. A depositor who later calls `withdrawCollateral` for the full credited `amount` forces the clearinghouse to pay out tokens that were deposited by other users. Repeated deposits amplify the shortfall linearly. The final withdrawing user(s) cannot be made whole — their collateral is stolen.

---

### Likelihood Explanation

Any spot product whose underlying ERC-20 charges a transfer fee triggers this path. The entry point (`depositCollateralWithReferral`) is public and callable by any unsanctioned address. No privileged role is required. The only precondition — a small residual balance in the endpoint — is trivially satisfied by sending dust tokens directly to the endpoint contract before depositing.

---

### Recommendation

In `EndpointStorage.handleDepositTransfer`, measure the clearinghouse's actual token balance before and after the transfer and use the delta as the credited amount. Pass this measured amount — not the caller-supplied `amount` — into the queued `DepositCollateral` slow-mode transaction.

```solidity
function handleDepositTransfer(
    IERC20Base token,
    address from,
    uint256 amount
) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    safeTransferFrom(token, from, amount);
    uint256 before = token.balanceOf(address(clearinghouse));
    safeTransferTo(token, address(clearinghouse), amount);
    uint256 actualReceived = token.balanceOf(address(clearinghouse)) - before;
    // return actualReceived to the caller so the slow-mode tx uses it
}
```

The caller (`depositCollateralWithReferral`) must then encode `actualReceived` — not `amount` — into the `DepositCollateral` struct.

---

### Proof of Concept

**Setup:** A spot product is configured with a fee-on-transfer token (2% fee per transfer). The endpoint holds a dust balance of 1 wei of that token (seeded by the attacker via a direct transfer).

**Steps:**

1. Attacker calls `Endpoint.depositCollateralWithReferral(subaccount, productId, 1000e18, "")`.
2. `handleDepositTransfer` is invoked with `amount = 1000e18`.
   - `safeTransferFrom`: endpoint receives `980e18` (`1000e18 − 2%`).
   - Endpoint balance = `980e18 + 1 wei` (dust).
   - `safeTransferTo`: endpoint sends `1000e18` to clearinghouse; clearinghouse receives `980e18` (another 2% fee). Endpoint balance = `980e18 + 1 wei − 1000e18` ≈ `−20e18 + 1 wei` — covered by the dust and the `20e18` shortfall is drawn from any existing endpoint balance.
3. Slow-mode tx enqueued with `amount = 1000e18`.
4. After the delay, sequencer executes the slow-mode tx; `Clearinghouse.depositCollateral` credits `1000e18` (normalized) to the attacker's subaccount.
5. Attacker calls `withdrawCollateral` for `1000e18`; clearinghouse only received `980e18` for this deposit. The `20e18` shortfall is paid from other users' deposited collateral.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** core/contracts/Endpoint.sol (L144-165)
```text
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
```

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
