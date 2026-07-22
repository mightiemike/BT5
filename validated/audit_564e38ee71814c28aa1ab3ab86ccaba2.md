### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual end-user, breaking allowlist enforcement on curated pools — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender`, so the extension checks the **router's address** instead of the actual end-user. This produces two fund-impacting failure modes: (1) allowlisted users cannot swap through the standard periphery path; (2) if the pool admin allowlists the router to work around this, every user on-chain bypasses the per-user curation gate.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`, the router calls `pool.swap()` on the user's behalf:

```solidity
// MetricOmmSimpleRouter.sol line 104-112
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),
        zeroForOne,
        amount,
        MetricOmmSwapPath.openLimit(zeroForOne),
        "",
        params.extensionDatas[i]
    );
```

The pool's `msg.sender` is now the **router**, not the end-user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Failure mode A — allowlisted users blocked from the router:**
A pool admin allowlists specific KYC'd users directly (`allowedSwapper[pool][user1] = true`). Those users call the router. The extension sees the router address, finds it not allowlisted, and reverts with `NotAllowedToSwap`. The standard periphery path is completely unusable for the curated pool's intended participants.

**Failure mode B — full allowlist bypass:**
To unblock router access, the pool admin allowlists the router address (`allowedSwapper[pool][router] = true`). Now every address on-chain can swap through the router regardless of individual allowlist status, because the extension only checks the router and finds it approved. The per-user curation gate is silently nullified.

---

### Impact Explanation

**Failure mode A** renders the standard periphery swap path (`MetricOmmSimpleRouter`) permanently broken for any pool using `SwapAllowlistExtension` with individual user entries. Allowlisted users must call the pool directly, which is not the supported UX and may not be possible for users relying on router-level slippage protection and multi-hop routing.

**Failure mode B** is a direct allowlist bypass: any unprivileged user can trade on a pool that was configured to restrict access to a curated set of counterparties. For pools holding concentrated LP positions or operating under regulatory/compliance constraints, this constitutes a loss of the intended access-control invariant and exposes LP funds to trades from disallowed counterparties.

---

### Likelihood Explanation

The router is the primary and documented periphery entry point for swaps. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will immediately encounter failure mode A. The admin workaround (allowlisting the router) is the natural response and directly triggers failure mode B. No special attacker capability is required — any user with a standard wallet can exploit failure mode B once the router is allowlisted.

---

### Recommendation

The `SwapAllowlistExtension` must check the **actual end-user identity**, not the intermediary caller. Two options:

1. **Check `recipient` instead of `sender`**: The `recipient` parameter is the address that receives output tokens and is set by the end-user. However, this is also not reliable for multi-hop paths where intermediate hops use `address(this)` as recipient.

2. **Require the router to forward the originating user**: Add a `swapperOverride` field to `extensionData` that the router populates with `msg.sender` before calling the pool. The extension reads this field and checks it against the allowlist. The pool's `onlyPool` guard on the extension ensures only the pool can invoke the check, and the router's callback context ensures the override cannot be spoofed by an arbitrary caller.

3. **Check `msg.sender` at the router level before calling the pool**: The router enforces the allowlist check itself before forwarding to the pool, removing the dependency on the extension seeing the correct actor.

---

### Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension as beforeSwap hook
// 2. Admin allowlists user1 (KYC'd user)
swapExtension.setAllowedToSwap(address(pool), user1, true);

// 3. user1 tries to swap through the router — REVERTS (router not allowlisted)
vm.prank(user1);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: user1,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// → reverts NotAllowedToSwap (router address checked, not user1)

// 4. Admin allowlists the router to "fix" the issue
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// 5. user2 (NOT allowlisted) swaps through the router — SUCCEEDS (bypass)
vm.prank(user2);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: user2,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// → succeeds: allowlist fully bypassed
```

**Root cause line references:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
