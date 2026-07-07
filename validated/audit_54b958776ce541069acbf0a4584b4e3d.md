### Title
Fee-on-Transfer Token Deposit Inflates Subaccount Balance Beyond Actual Clearinghouse Holdings — (File: `core/contracts/EndpointStorage.sol`)

---

### Summary

`handleDepositTransfer` performs a two-hop token transfer (User → Endpoint → Clearinghouse) using the caller-supplied `amount` for both legs. The slow-mode transaction queued immediately after records that same `amount`. When the sequencer later processes it, `Clearinghouse.depositCollateral` credits the subaccount with `amount` (scaled). If the token deducts a fee on any leg of the transfer, the Clearinghouse receives fewer tokens than the amount credited to the subaccount, permanently inflating internal balances relative to actual holdings.

---

### Finding Description

The deposit entry point is `Endpoint.depositCollateralWithReferral`: [1](#0-0) 

It calls `handleDepositTransfer` with the raw caller-supplied `amount`: [2](#0-1) 

`handleDepositTransfer` executes two sequential transfers using the same `amount` value:

```
safeTransferFrom(token, from, amount);           // leg 1: User → Endpoint
safeTransferTo(token, address(clearinghouse), amount); // leg 2: Endpoint → Clearinghouse
```

Neither leg measures the actual tokens received; both use the caller-supplied `amount` verbatim. The slow-mode transaction is then enqueued with `amount`: [3](#0-2) 

When the sequencer processes the slow-mode transaction, `Clearinghouse.depositCollateral` derives `amountRealized` directly from `txn.amount` (the originally supplied value) and credits the subaccount: [4](#0-3) 

No balance-before/after measurement is ever taken. The protocol assumes the Clearinghouse received exactly `amount` tokens, which is false for any fee-on-transfer token.

The same flaw is present in the `DirectDepositV1.creditDeposit()` path, which reads the DDA's token balance and passes it directly as `uint128(balance)` to `depositCollateralWithReferral`: [5](#0-4) 

---

### Impact Explanation

**Broken invariant:** `Σ subaccount balances for product P ≤ token.balanceOf(clearinghouse)` for every registered spot product.

For a fee-on-transfer token with fee rate `f`:

- Leg 2 (Endpoint → Clearinghouse) delivers `amount × (1 − f)` tokens to the Clearinghouse.
- The subaccount is credited with `amount × multiplier` (the full amount, scaled).
- The gap `amount × f × multiplier` is phantom balance — it exists in the accounting but has no token backing.

Across multiple depositors the phantom balance accumulates. When users withdraw, `withdrawCollateral` transfers the full recorded amount out of the Clearinghouse: [6](#0-5) 

The Clearinghouse's actual token balance is exhausted before all subaccounts can withdraw, causing the last withdrawers to receive nothing or causing the withdrawal to revert entirely. The protocol becomes insolvent for that product.

---

### Likelihood Explanation

**Medium.** The preconditions are:

1. A fee-on-transfer (or rebase/deflationary) token is registered as a spot product. Product registration is owner-controlled, but the protocol imposes no technical guard against such tokens — any ERC-20 satisfying the `IERC20Base` interface can be registered.
2. Any unprivileged user then calls `depositCollateral` or `depositCollateralWithReferral` with that product ID. No special role is required.

For leg 2 to succeed when leg 1 already consumed a fee (leaving the Endpoint short), the Endpoint needs a residual balance of the token. This can arise from: direct token transfers to the Endpoint, dust from prior partial operations, or tokens with asymmetric fee schedules (e.g., fee only on transfers *to* the Clearinghouse's address). The `DirectDepositV1` path is additionally reachable by any caller of `creditDeposit()` with no access control.

---

### Recommendation

Replace the fixed-`amount` two-hop pattern in `handleDepositTransfer` with a balance-delta measurement:

```solidity
function handleDepositTransfer(IERC20Base token, address from, uint256 amount) internal {
    require(address(token) != address(0), ERR_INVALID_PRODUCT);
    uint256 before = token.balanceOf(address(clearinghouse));
    safeTransferFrom(token, from, amount);
    safeTransferTo(token, address(clearinghouse), amount);
    uint256 actualReceived = token.balanceOf(address(clearinghouse)) - before;
    // pass actualReceived (not amount) into the slow-mode tx
}
```

The `DepositCollateral` slow-mode transaction and `Clearinghouse.depositCollateral` must then use the measured `actualReceived` value rather than the caller-supplied `amount`. Alternatively, enforce a token whitelist at the product-registration layer that excludes all fee-on-transfer, rebase, and deflationary tokens.

---

### Proof of Concept

1. Owner registers a fee-on-transfer token `FTT` (2% fee on every transfer) as spot product `P`.
2. Alice calls `Endpoint.depositCollateral("alice", P, 1000e18)`.
3. `handleDepositTransfer` executes:
   - `safeTransferFrom(FTT, alice, 1000e18)` → Endpoint receives `980e18` (2% fee).
   - `safeTransferTo(FTT, clearinghouse, 1000e18)` → Endpoint tries to send `1000e18` but only holds `980e18`. If the Endpoint has ≥ `20e18` residual balance (from prior dust or direct transfer), the call succeeds; Clearinghouse receives `980e18`.
4. Slow-mode tx is queued with `amount = 1000e18`.
5. Sequencer processes it: `amountRealized = 1000e18 × multiplier`; Alice's subaccount is credited with `1000e18` (scaled).
6. Clearinghouse actually holds `980e18` tokens.
7. Alice withdraws `1000e18` (scaled) — `withdrawCollateral` sends `1000e18` tokens out, leaving the Clearinghouse with a deficit of `20e18`.
8. Bob, who deposited `1000e18` and is credited `1000e18`, attempts to withdraw and finds the Clearinghouse balance insufficient; his withdrawal reverts. [2](#0-1) [7](#0-6) [8](#0-7)

### Citations

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

**File:** core/contracts/Clearinghouse.sol (L391-421)
```text
    function withdrawCollateral(
        bytes32 sender,
        uint32 productId,
        uint128 amount,
        address sendTo,
        uint64 idx
    ) public virtual onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(sender), ERR_UNAUTHORIZED);
        require(amount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        ISpotEngine spotEngine = _spotEngine();
        IERC20Base token = IERC20Base(spotEngine.getConfig(productId).token);
        require(address(token) != address(0));

        if (sendTo == address(0)) {
            sendTo = address(uint160(bytes20(sender)));
        }

        handleWithdrawTransfer(token, sendTo, amount, idx);

        int256 multiplier = int256(10**(MAX_DECIMALS - _decimals(productId)));
        int128 amountRealized = -int128(amount) * int128(multiplier);
        spotEngine.updateBalance(productId, sender, amountRealized);
        spotEngine.assertUtilization(productId);

        IProductEngine.HealthType healthType = sender == X_ACCOUNT
            ? IProductEngine.HealthType.PNL
            : IProductEngine.HealthType.INITIAL;

        require(getHealth(sender, healthType) >= 0, ERR_SUBACCT_HEALTH);
        emit ModifyCollateral(amountRealized, sender, productId);
    }
```

**File:** core/contracts/DirectDepositV1.sol (L83-99)
```text
    function creditDeposit() external {
        uint32[] memory productIds = spotEngine.getProductIds();
        for (uint256 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            address tokenAddr = spotEngine.getToken(productId);
            require(tokenAddr != address(0), "Invalid productId.");
            IIERC20Base token = IIERC20Base(tokenAddr);
            uint256 balance = token.balanceOf(address(this));
            if (balance != 0) {
                token.approve(address(endpoint), balance);
                endpoint.depositCollateralWithReferral(
                    subaccount,
                    productId,
                    uint128(balance),
                    "-1"
                );
            }
```
