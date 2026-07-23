### Title
SwapAllowlistExtension gates the router's address instead of the real swapper, letting any unprivileged user bypass the per-user swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is `msg.sender` to the pool — the router contract — not the EOA that initiated the trade. When a pool admin allowlists `MetricOmmSimpleRouter` to permit router-mediated swaps, every user of the router inherits that allowance, completely defeating the per-user gate.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every configured extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged:

```solidity
// ExtensionCalling.sol
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on that forwarded `sender`:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

Here `msg.sender` is the pool and `sender` is whoever called the pool — the router when the user goes through `MetricOmmSimpleRouter`. The router calls `pool.swap(recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
```

There is no mechanism in the router to inject the originating EOA's address into the extension path. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Bypass path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists specific user addresses.
2. Pool admin also calls `setAllowedToSwap(pool, router, true)` so that router-mediated swaps work for allowlisted users.
3. Any non-allowlisted EOA calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutput`).
4. The extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and passes.
5. The non-allowlisted user's swap executes on the restricted pool.

The same bypass applies to `addLiquidityWeighted` probe calls that go through the adder, but the swap path is the most direct and economically impactful route.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's primary mechanism for restricting swap access on a per-pool basis. Pools may be restricted for regulatory compliance, to protect LPs from specific counterparties, or to limit access during a guarded launch. Once the router is allowlisted (a natural and expected admin action to enable normal UX), the gate is fully open to every user of the router. Non-allowlisted actors can execute arbitrary swaps against the pool, exposing LPs to trades the pool admin explicitly intended to block. If the restriction was meant to exclude actors known to extract value from LPs (e.g., high-frequency arbitrageurs), LP principal is directly at risk.

---

### Likelihood Explanation

- The router is a standard, publicly deployed periphery contract. Pool admins are expected to allowlist it to give allowlisted users a normal swap UX.
- No special privilege, flash loan, or contract deployment is required — any EOA can call `MetricOmmSimpleRouter.exactInputSingle`.
- The bypass is silent: the extension emits no event and the swap completes normally, so the pool admin has no on-chain signal that the gate was circumvented.

---

### Recommendation

The extension must check the identity of the economic actor, not the intermediary. Two options:

1. **Check `sender` against the allowlist and require direct pool calls** — document that router-mediated swaps are incompatible with `SwapAllowlistExtension`.
2. **Pass the originating user through `extensionData`** — the router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the extension to trust the router's encoding, which reintroduces a trust assumption.

The cleanest fix is option 1: `SwapAllowlistExtension` should document that it gates `msg.sender` to the pool, and the router should not be allowlisted on pools using this extension. Alternatively, the extension should check `sender` (the pool's `msg.sender`) and the pool admin should allowlist individual users who call the pool directly, not the router.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // alice is the only intended swapper
  allowedSwapper[pool][router] = true   // admin allowlists router for alice's UX

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool:      pool,
        recipient: bob,
        zeroForOne: true,
        amountIn:  X,
        ...
    })

  pool.swap(bob, true, X, ...) is called with msg.sender = router
  _beforeSwap(router, bob, ...) is dispatched
  SwapAllowlistExtension.beforeSwap(router, bob, ...) checks:
    allowedSwapper[pool][router] == true  → passes
  bob's swap executes on the restricted pool
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
