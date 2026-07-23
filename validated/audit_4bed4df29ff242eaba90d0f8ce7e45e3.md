### Title
`SwapAllowlistExtension` Checks Intermediary `sender` Instead of Ultimate User, Allowing Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter — the **direct caller of `pool.swap()`**. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If the pool admin allowlists the router (the only way to enable router-based swaps), every unpermissioned user can bypass the swap allowlist by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its allowlist check as follows: [1](#0-0) 

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

Here `msg.sender` is the pool (correct) and `sender` is the first argument the pool passes — which is `msg.sender` at the pool level, i.e., whoever called `pool.swap()`.

The pool propagates `sender` verbatim through `ExtensionCalling._beforeSwap`: [2](#0-1) 

When a user swaps through `MetricOmmSimpleRouter`, the call chain is:

```
User → Router.swap(...) → Pool.swap(sender = Router, recipient = User)
                                         ↓
                          Extension.beforeSwap(sender = Router)
```

The extension evaluates `allowedSwapper[pool][Router]`. For router-based swaps to work at all, the pool admin **must** allowlist the router. Once the router is allowlisted, the check passes for **every** user who routes through it, regardless of whether that user is individually permitted.

The `recipient` parameter (the actual user receiving output tokens) is silently discarded — it is the unnamed second argument in the override: [3](#0-2) 

The base class `BaseMetricExtension.beforeSwap` carries `onlyPool` but the override drops it and is `view`, so there is no secondary guard to catch the mismatch: [4](#0-3) 

---

### Impact Explanation

A pool admin who deploys a permissioned pool (e.g., KYC-gated, institutional-only) and attaches `SwapAllowlistExtension` cannot simultaneously allow router-based swaps **and** restrict swaps to specific users. Allowlisting the router — the only way to make the router work — opens the pool to all users. Any unpermissioned address can trade against LP funds in a pool that was explicitly configured to be restricted. This is an admin-boundary break: the pool admin's configured allowlist is bypassed by an unprivileged path through the router.

---

### Likelihood Explanation

Medium. The trigger requires the pool admin to allowlist the router, which is a natural and expected operational step for any pool that wants to support the standard periphery swap path. The bypass is then available to any address with no further preconditions.

---

### Recommendation

Check the **actual user** rather than the intermediary. Concretely:

1. **Check `recipient`** — the address receiving output tokens is the true economic counterparty and is already available as the second parameter of `beforeSwap`. Replace the `sender` check with a `recipient` check, or check both.
2. **Pass the real user via `extensionData`** — the router can encode the originating user in `extensionData`; the extension can decode and verify it.
3. **Document the limitation** — if checking `sender` is intentional (gating by router identity rather than user identity), the NatSpec must state this explicitly so pool admins understand that allowlisting the router grants access to all router users.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` attached to `BEFORE_SWAP_ORDER`.
2. Pool admin allowlists the router: `setAllowedToSwap(pool, router, true)`.
3. Non-allowlisted user `Bob` calls `router.swap(pool, ...)`.
4. Router calls `pool.swap(sender = router, recipient = Bob, ...)`.
5. Pool calls `extension.beforeSwap(sender = router, recipient = Bob, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → returns selector.
7. Bob's swap executes against LP funds in a pool he was never individually permitted to access. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
