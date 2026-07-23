### Title
`SwapAllowlistExtension` Allowlist Bypassed via Router — Any User Can Swap on Restricted Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to work on a restricted pool), every unpermissioned user can bypass the allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the value the pool passes — which is `msg.sender` of the pool's own `swap()` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← this becomes `sender` in the extension
  recipient,
  ...
  extensionData
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
  );
```

The pool's `msg.sender` is the router, so `sender` forwarded to `beforeSwap` is the router address. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

For any allowlisted user to swap via the router, the pool admin must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router — including users who were never individually allowlisted. The extension has no way to recover the original `msg.sender` of the router call.

The same structural issue applies to `exactInput` and `exactOutput` multi-hop paths, which also call `pool.swap` with `msg.sender = router`.

---

### Impact Explanation

The `SwapAllowlistExtension` is the sole on-chain mechanism for restricting which addresses may trade on a pool. A bypass means:

- Unpermissioned users can execute swaps on pools intended to be restricted (e.g., institutional pools, KYC-gated pools, or pools in a controlled bootstrapping phase).
- LP funds are exposed to toxic flow or adversarial trading from actors the pool admin explicitly excluded.
- The pool admin cannot simultaneously support router-mediated swaps for allowlisted users and block non-allowlisted users from using the same router — the two goals are mutually exclusive under the current design.

This is a direct broken-core-functionality impact: the allowlist guard is rendered ineffective for any pool that supports the public router.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the standard, publicly deployed periphery contract. Any user can call it permissionlessly.
- A pool admin who wants allowlisted users to be able to use the router (a normal operational requirement) must add the router to the allowlist, which immediately opens the bypass to all users.
- No special privileges, flash loans, or unusual token behavior are required. A single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the immediate caller of the pool. Two approaches:

1. **Pass the originating user through the router**: Have the router encode the original `msg.sender` in `extensionData`, and have the extension decode and check that address. This requires a trusted router or a signed attestation.

2. **Check `sender` against the router and then verify the router's stored payer**: The router already stores the payer in transient storage (`_getPayer()`). The extension could call back into the router to retrieve the true payer. This couples the extension to the router implementation.

3. **Restrict the allowlist to direct pool callers only** (no router support): Document that allowlisted pools must not add the router to the allowlist and that allowlisted users must call `pool.swap` directly.

The cleanest production fix is option 1: the router should forward the original `msg.sender` in a standardized field of `extensionData`, and the extension should decode and verify it, with the router's address itself being trusted as the forwarder.

---

### Proof of Concept

**Setup:**
- Pool is deployed with `SwapAllowlistExtension` configured.
- Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only permitted swapper.
- Pool admin calls `setAllowedToSwap(pool, router, true)` — required so Alice can use the router.

**Attack:**
1. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
2. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` with `msg.sender = router`.
3. Pool calls `_beforeSwap(router, ...)`.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to `SwapAllowlistExtension.beforeSwap`.
5. Extension evaluates `allowedSwapper[pool][router]` → `true` (admin added the router).
6. Swap proceeds. Bob has bypassed the allowlist.

**Invariant broken:** `allowedSwapper[pool][bob]` is `false`, yet Bob's swap settles successfully, draining pool reserves in exchange for Bob's input tokens. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
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
