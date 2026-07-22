### Title
`DepositAllowlistExtension` guards LP position holder (`owner`) instead of the actual depositor (`sender`), allowing any unprivileged address to bypass the deposit allowlist — (`File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the address that actually calls `addLiquidity` and provides tokens via callback) and instead evaluates the allowlist against `owner` (the LP position recipient). Because `owner` is a free caller-supplied parameter with no ownership requirement, any non-allowlisted address can bypass the guard by nominating any already-allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook:

```
_beforeAddLiquidity(msg.sender /*sender*/, owner /*owner*/, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first positional argument — `sender`, the entity that will pay tokens — is **unnamed and discarded**. The allowlist lookup is performed against `owner` instead:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The `SwapAllowlistExtension` demonstrates the correct pattern — it names and checks `sender`:

```solidity
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [4](#0-3) 

The inconsistency confirms the `DepositAllowlistExtension` check is applied to the wrong address.

`addLiquidity` imposes **no ownership requirement on `owner`** — any caller may supply any address:

```solidity
function addLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, ...)
    external nonReentrant(PoolActions.ADD_LIQUIDITY) ...
``` [5](#0-4) 

`removeLiquidity` does enforce `msg.sender == owner`, so the attacker cannot reclaim the deposited tokens — but the allowlist guard is fully defeated regardless. [6](#0-5) 

---

### Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for controlling which addresses may inject liquidity. With the guard checking `owner` instead of `sender`:

- Any non-allowlisted address can add liquidity to a restricted pool by nominating any allowlisted address as `owner`.
- Tokens flow from the non-allowlisted caller into the pool's bin accounting; the pool's `binTotals` and individual `BinState` balances are updated with unauthorized capital.
- The pool admin's access-control boundary is completely bypassed by an unprivileged path — matching the "Admin-boundary break" impact class.
- Downstream, unauthorized liquidity shifts `curBinIdx`/`curPosInBin` and alters the marginal price seen by legitimate swappers, potentially degrading execution quality for existing LPs and traders. [7](#0-6) 

---

### Likelihood Explanation

Exploitation requires only a single `addLiquidity` call with any allowlisted address as `owner`. No privileged access, no special token, no flash loan, and no front-running is needed. The allowlisted address need not cooperate. Any on-chain observer can read `allowedDepositor` to find a valid `owner` to nominate.

---

### Recommendation

Name and check `sender` (the first parameter) instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`. [8](#0-7) 

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached to the `beforeAddLiquidity` hook.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is allowlisted.
3. Bob (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(
       alice,          // owner — allowlisted, check passes
       salt,
       deltas,
       callbackData,   // Bob's contract pays tokens here
       extensionData
   );
   ```
4. `DepositAllowlistExtension.beforeAddLiquidity` evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes; Bob's tokens enter the pool's bin accounting; Alice receives the LP shares.
6. Bob has injected unauthorized liquidity into a restricted pool. The pool admin's allowlist is defeated. [3](#0-2) [9](#0-8)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-188)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
