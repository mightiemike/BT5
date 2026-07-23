### Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual end user. If the router is allowlisted on a curated pool (the only way to support router-based swaps), every user—including non-allowlisted ones—can bypass the per-user access control by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`_beforeSwap()` in `ExtensionCalling` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point), the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap()` is the router, so `sender` delivered to the extension is the router address—not the actual end user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`.

A pool admin who wants to support router-based swaps on a curated pool must allowlist the router address. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for every call that arrives through the router, regardless of who the actual end user is. The per-user allowlist is completely defeated.

The invariant stated in the protocol's own audit domain description confirms this is the intended protection boundary:

> "A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it." [5](#0-4) 

---

### Impact Explanation

Any non-allowlisted user can trade on a curated pool that was configured to restrict access to specific counterparties (e.g., KYC-gated, institutional-only, or whitelist-only pools). The attacker receives real token output from the pool; the pool's LPs bear the economic exposure to an actor the pool admin explicitly excluded. This is a direct loss of the access-control guarantee that LPs and the pool admin relied upon when choosing to deploy a curated pool.

---

### Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router (a natural operational step for any curated pool that wants to support the standard periphery). No privileged access, no special tokens, and no multi-step setup is needed by the attacker—a single `exactInputSingle` call through the router suffices. The router is a publicly deployed, permissionless contract.

---

### Recommendation

The extension must gate on the actual end user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop, and the extension decodes and checks it. This requires a coordinated change to the router and the extension.

2. **Check `sender` only when `sender` is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, require the real user to be encoded in `extensionData` and check that instead.

Either way, the extension must not treat the router address as the identity to gate.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps for allowlisted users.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice; Bob is not allowlisted.
4. Bob (non-allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, recipient, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes successfully, bypassing the per-user allowlist entirely. [6](#0-5) [7](#0-6)

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

**File:** generate_scanned_questions.py (L733-738)
```python
            title="allowlist bypass",
            question_focus="a curated pool's allowlist can be bypassed through a public router or liquidity-adder path",
            exploit="Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.",
            invariant="A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it.",
            impact="High direct loss or curation failure if disallowed users can still trade or deposit.",
        ),
```
