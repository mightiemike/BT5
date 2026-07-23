### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual end user. If the pool admin allowlists the router (the natural step to enable router-based swaps for permitted users), every unprivileged user can bypass the restriction by calling the router.

---

### Finding Description

**Extension check (the guard):** [1](#0-0) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool and `sender` is the first argument the pool passes into the hook.

**What the pool passes as `sender`:** [2](#0-1) 

```solidity
_beforeSwap(
    msg.sender,   // ← this becomes `sender` in the extension
    recipient,
    ...
);
```

`msg.sender` inside `MetricOmmPool.swap()` is whoever called `swap()` — the router when users go through it.

**What the router passes to the pool:** [3](#0-2) 

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

The router calls `pool.swap()` directly; the pool records `msg.sender = router` and forwards it as `sender` to every extension.

**The broken invariant:**

The allowlist maps `pool → swapper → bool`. The intended swapper is the end user. But the extension receives the router's address as `sender`, so:

| Admin intent | What extension sees | Result |
|---|---|---|
| Allowlist `alice`, `bob` only | `sender = router` | Router not listed → all router swaps revert |
| Allowlist `alice`, `bob`, **and router** | `sender = router` | Router listed → **any user** can swap via router |

Neither configuration achieves "only `alice` and `bob` can swap through the router." The second configuration — the only one that makes the router usable — silently removes the per-user gate entirely.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the position owner, passed explicitly by the caller and forwarded unchanged by the pool): [4](#0-3) 

The deposit extension checks the position owner — the actual user — so it is not affected. The swap extension checks the immediate pool caller — the router — so it is broken.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` (e.g., KYC-gated, institutional-only, or whitelist-only pool) and with the router allowlisted loses its access control entirely for router-based swaps. Any unprivileged address can call `router.exactInputSingle()` and trade in the restricted pool. This breaks the core pool access-control invariant and constitutes an admin-boundary break via an unprivileged path.

---

### Likelihood Explanation

The scenario is realistic and likely: a pool admin who deploys a `SwapAllowlistExtension` and wants permitted users to access the standard router will naturally allowlist the router address. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call.

---

### Recommendation

The extension must identify the actual end user, not the intermediary. Two options:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires router cooperation and a trusted encoding convention.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the real user, but this is not reliable for multi-hop or third-party-recipient flows.

3. **Structural fix**: Add a dedicated "on-behalf-of" field to the swap call that the pool forwards to extensions, distinct from the callback payer. The router would populate it with `msg.sender`.

The simplest safe fix is option 1: require that any router wishing to be allowlisted encodes the actual swapper in `extensionData`, and have the extension decode and check that address instead of `sender`.

---

### Proof of Concept

1. Pool is deployed with `SwapAllowlistExtension` as `extension1` in `beforeSwap` order.
2. Admin calls `extension.setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Admin calls `extension.setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: restrictedPool,
       ...
   }));
   ```
5. Pool calls `_beforeSwap(msg.sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
7. Bob's swap executes in the restricted pool despite not being on the allowlist. [5](#0-4) [2](#0-1) [6](#0-5) [3](#0-2)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
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
