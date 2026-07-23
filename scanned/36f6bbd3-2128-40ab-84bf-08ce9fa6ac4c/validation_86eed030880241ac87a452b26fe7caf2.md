### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the pool admin allowlists the router (a natural step to let allowlisted users access the router), every unprivileged user can bypass the per-address swap gate by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called `pool.swap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actualUser]`. The actual user's address is never inspected.

A pool admin who wants allowlisted users to be able to use the router must allowlist the router itself. Once the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for **every** caller of the router, including addresses the admin never intended to permit.

The same path exists for `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap` with `msg.sender == router`: [5](#0-4) 

The existing test suite only exercises `TestCaller` (a direct pool caller), so the router-mediated bypass is never asserted: [6](#0-5) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional participants, or whitelisted bots) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist guard — the only on-chain enforcement mechanism for swap access control — is rendered ineffective for the public router path. This is a broken core pool functionality impact: the configured access-control extension does not gate the actor the pool admin intended to gate.

---

### Likelihood Explanation

The scenario is directly reachable without any privileged action by the attacker. The only prerequisite is that the pool admin allowlists the router — a natural and expected step whenever the admin wants allowlisted users to be able to use the standard periphery router. The admin has no way to simultaneously allow allowlisted users to use the router and block non-allowlisted users from doing the same, because the extension cannot distinguish between different callers of the router.

---

### Recommendation

The `beforeSwap` hook should gate the **economically relevant actor** — the address that initiated the swap and will pay the input tokens — not the intermediate contract that called `pool.swap`. Two viable approaches:

1. **Pass the real payer through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires the extension to trust the router's encoding, which introduces its own trust assumptions.

2. **Check `sender` against a router registry and then verify the real user via a signed payload or separate allowlist lookup**: More complex but fully trustless.

At minimum, document clearly that allowlisting the router grants swap access to all router users, and that pools requiring per-user gating must not allowlist the router.

---

### Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension as beforeSwap extension
  alice = allowlisted address  (allowedSwapper[pool][alice] = true)
  bob   = non-allowlisted address
  router = MetricOmmSimpleRouter

Step 1 — admin allowlists alice and the router so alice can use the router:
  swapExtension.setAllowedToSwap(pool, alice,  true)   // alice can swap directly
  swapExtension.setAllowedToSwap(pool, router, true)   // router can call pool.swap

Step 2 — bob calls the router (bob is NOT allowlisted):
  router.exactInputSingle({pool: pool, recipient: bob, ...})

Step 3 — inside pool.swap:
  _beforeSwap(msg.sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; bob receives output tokens

Result: bob successfully swaps on a pool he was never permitted to access.
``` [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
