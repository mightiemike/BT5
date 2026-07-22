### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool passes `msg.sender` (the direct caller of `pool.swap()`) as `sender`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller, so `sender` becomes the router address. If the pool admin allowlists the router to enable router-mediated swaps, every user — including explicitly disallowed ones — can bypass the allowlist by routing through the public router.

---

### Finding Description

**Call chain for a direct swap:**
```
User → pool.swap() → _beforeSwap(msg.sender=User, ...) → SwapAllowlistExtension.beforeSwap(sender=User)
```
The allowlist correctly checks `allowedSwapper[pool][User]`.

**Call chain for a router-mediated swap:**
```
User → MetricOmmSimpleRouter.exactInputSingle() → pool.swap() → _beforeSwap(msg.sender=Router, ...) → SwapAllowlistExtension.beforeSwap(sender=Router)
```
The allowlist checks `allowedSwapper[pool][Router]`, not `allowedSwapper[pool][User]`.

The pool passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` argument verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

The router calls `pool.swap()` directly, making itself the `sender` the extension sees: [4](#0-3) 

For the pool to be usable via the router at all, the pool admin must add the router to `allowedSwapper[pool]`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** user who routes through it, regardless of whether that user is individually blocked.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners, or protocol-controlled contracts) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The disallowed user executes swaps against LP liquidity at live oracle prices, draining value from LPs who deposited under the assumption that only trusted counterparties could trade. This is a direct loss of LP principal and a complete failure of the pool's curation invariant.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is a public, permissionless periphery contract deployed at a known address. Any user aware of the router can exploit this without any privileged access, special setup, or front-running. The only precondition is that the pool admin has allowlisted the router — a necessary step for the pool to be usable via the standard periphery. Pools that configure `SwapAllowlistExtension` but also want router support are the exact target, and the exploit requires a single standard router call.

---

### Recommendation

The allowlist must gate the **economically relevant actor** — the end-user — not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Check `recipient` instead of `sender`** — the recipient is the address that receives output tokens and is set by the end-user, not the router. However, this changes the semantic of the guard.

2. **Pass the original `msg.sender` through the router as part of `extensionData`** and have the extension decode and verify it. The router would encode `msg.sender` into `extensionData` before forwarding to the pool, and the extension would decode and check that address. This requires a coordinated extension + router design.

3. **Require direct pool interaction** for allowlisted pools — document that pools using `SwapAllowlistExtension` must not allowlist the router, and the router must not be used for such pools. This is operationally fragile.

The cleanest fix is option 2: the router encodes its `msg.sender` into `extensionData`, and `SwapAllowlistExtension` decodes and checks that address when present, falling back to `sender` otherwise.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
  - Pool admin calls setAllowedToSwap(pool, alice, false)   // alice is explicitly blocked

Attack:
  - alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=alice, ...)
  - pool._beforeSwap(sender=router, ...)
  - SwapAllowlistExtension.beforeSwap(sender=router):
      allowedSwapper[pool][router] == true  →  check passes
  - Alice's swap executes against LP liquidity at live oracle prices.
  - Alice receives output tokens; LPs bear the loss.

Result: alice, an explicitly disallowed address, successfully swaps on a curated pool.
``` [5](#0-4) [6](#0-5) [4](#0-3)

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
