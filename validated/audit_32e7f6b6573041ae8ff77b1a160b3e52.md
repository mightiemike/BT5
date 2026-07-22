### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument passed by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router contract**, not the actual user. The allowlist therefore gates the router's address, not the real swapper. A pool admin who allowlists the router to support normal router-mediated swaps inadvertently opens the gate to every user on earth; a pool admin who does not allowlist the router silently blocks all legitimate allowlisted users from using the supported periphery path.

---

### Finding Description

`MetricOmmPool.swap()` captures `msg.sender` and forwards it verbatim as the `sender` argument to every `beforeSwap` extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    zeroForOne,
    ...
);
```

`SwapAllowlistExtension.beforeSwap()` then checks that exact value against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct key) and `sender` is the value the pool forwarded — the router address.

`MetricOmmSimpleRouter` calls the pool directly for every hop:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 104-112
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
    .swap(
        i == last ? params.recipient : address(this),
        zeroForOne,
        amount,
        ...
        params.extensionDatas[i]
    );
```

The pool therefore sees `msg.sender == address(router)`, and the extension checks `allowedSwapper[pool][router]` — never the EOA that initiated the transaction.

Two broken outcomes follow:

| Admin configuration | Outcome |
|---|---|
| Router **not** allowlisted | Every allowlisted EOA is silently blocked when using the router; they must call the pool directly, breaking the supported periphery path. |
| Router **allowlisted** (to fix the above) | Every address on chain can swap through the router, defeating the entire allowlist. |

There is no configuration that simultaneously allows allowlisted EOAs through the router and blocks non-allowlisted ones.

---

### Impact Explanation

A curated pool that deploys `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that protection entirely the moment the pool admin allowlists the router. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()` and the extension will pass them through. This is a direct, fund-impacting bypass of a core access-control mechanism: unauthorized users can execute swaps against a pool that was explicitly configured to exclude them, draining liquidity at oracle-derived prices.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point documented and deployed by the protocol. Pool admins who configure a swap allowlist will naturally expect it to work through the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call the router. The only precondition is that the pool admin has allowlisted the router (a near-certain operational step once they discover that allowlisted users cannot swap through it).

---

### Recommendation

The extension must check the economically relevant actor — the EOA that initiated the transaction — not the intermediate router. Two sound approaches:

1. **Pass `tx.origin` as an additional argument** from the pool to the extension (breaks composability and is generally discouraged).

2. **Have the router forward the real user identity in `extensionData`** and have the extension decode and verify it (requires a signed or authenticated payload).

3. **Check `sender` against the allowlist but also accept the router as a transparent forwarder**: the router stores the real payer in transient storage (`_setNextCallbackContext` already records `msg.sender` as the payer). Expose a `realSender()` view on the router and have the extension query it when `sender == router`. This is the least invasive fix given the existing architecture.

The cleanest long-term fix is option 3: the router already tracks the originating user in `_setNextCallbackContext`; the extension can call back into the router to retrieve the real payer and check that address against the allowlist.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as `EXTENSION_1`, `beforeSwap` order = `1`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` (required so router-mediated swaps don't revert for everyone).
3. Unprivileged EOA `attacker` (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Pool calls `_beforeSwap(msg.sender=router, ...)`.
5. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
6. Attacker's swap executes against the curated pool despite never being allowlisted.

Alternatively, if the admin does **not** allowlist the router:

1. Pool admin calls `setAllowedToSwap(pool, alice, true)`.
2. Alice calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
3. Pool calls `_beforeSwap(msg.sender=router, ...)`.
4. Extension evaluates `allowedSwapper[pool][router] == false` → `NotAllowedToSwap` revert.
5. Alice, a legitimately allowlisted user, cannot use the supported periphery path.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```
