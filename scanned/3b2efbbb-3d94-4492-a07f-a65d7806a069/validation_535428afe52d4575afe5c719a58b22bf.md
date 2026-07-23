### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` — and therefore the `sender` forwarded to the extension — is the **router contract**, not the actual end user. If the pool admin allowlists the router (which is required for any legitimate user to swap through it), every non-allowlisted user can bypass the per-user gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
  external view override returns (bytes4)
{
  if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
  }
  return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct — the extension uses it as the pool key), and `sender` is the address the pool passes as the first argument to `beforeSwap`. The pool sets that argument to its own `msg.sender` at the time `swap` is called:

```solidity
// MetricOmmPool.sol L230-239 (beforeSwap call)
_beforeSwap(
  msg.sender,   // <-- this becomes `sender` in the extension
  recipient,
  ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)` directly. At that point `msg.sender` to the pool is the **router contract address**, so `sender` forwarded to the extension is the router, not the end user.

The extension then evaluates `allowedSwapper[pool][router]`. For any legitimate user to swap through the router, the pool admin must add the router to the allowlist. Once the router is allowlisted, **every** address — including addresses the admin explicitly excluded — can bypass the per-user gate simply by routing through the router.

The `ExtensionCalling._beforeSwap` dispatcher faithfully forwards `msg.sender` of the pool call:

```solidity
// ExtensionCalling.sol L149-177
function _beforeSwap(address sender, ...) internal {
  _callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
  );
}
``` [3](#0-2) 

There is no mechanism in the router or the extension to recover the original end-user address.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) is fully bypassable by any user who routes through `MetricOmmSimpleRouter`. The allowlist guard — the only access-control layer on the swap path — is rendered ineffective. Any non-allowlisted user can execute swaps, draining LP value or executing trades the pool admin explicitly prohibited. This is a direct loss of the access-control invariant with fund-impacting consequences (unauthorized swaps alter pool balances and LP positions).

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap interface. Any pool that uses `SwapAllowlistExtension` and also needs to support router-mediated swaps (the normal user flow) must allowlist the router, making the bypass trivially reachable by any unprivileged address. No special permissions, flash loans, or complex setup are required — a single call to the router suffices.

---

### Recommendation

The extension must gate the **economic actor**, not the immediate caller. Two sound approaches:

1. **Pass the original initiator through the router**: Have the router encode the end-user address in `extensionData` and have the extension decode and check it. This requires a trusted router assumption.
2. **Check `tx.origin` as a fallback** (only acceptable if the pool is not used in any meta-transaction context): replace `sender` with `tx.origin` inside the extension when `sender` is a known router.
3. **Preferred — check `sender` at the pool level before the extension call**: Add a pool-level allowlist that the router cannot bypass, or require the router to pass the real user address as a verified parameter.

The cleanest fix is for `SwapAllowlistExtension.beforeSwap` to also accept and verify a user-supplied address from `extensionData` when `sender` is a known intermediary, or for the router to be excluded from the allowlist pattern entirely and replaced with a direct-call-only model for allowlisted pools.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin allowlists `alice` (`allowedSwapper[pool][alice] = true`) and explicitly does **not** allowlist `bob`.
3. Admin also allowlists the router (`allowedSwapper[pool][router] = true`) so that `alice` can use the router.
4. `bob` calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. The router calls `pool.swap(recipient, ...)` — `msg.sender` to the pool is the router.
6. The pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → **passes**.
7. `bob`'s swap executes despite being explicitly excluded from the allowlist. [1](#0-0) [4](#0-3) [3](#0-2)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
