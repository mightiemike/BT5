The vulnerability claim is real. Let me trace through the exact code path.

## Code Path Analysis

**Pool `addLiquidity`** passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension: [1](#0-0) 

**Extension calling** encodes both `sender` and `owner` and forwards them: [2](#0-1) 

**`DepositAllowlistExtension.beforeAddLiquidity`** silently discards `sender` (first parameter is unnamed `address,`) and checks `owner` instead: [3](#0-2) 

The check `allowedDepositor[msg.sender][owner]` translates to `allowedDepositor[pool][owner]` — it asks "is the LP position recipient on the allowlist?" not "is the actual caller on the allowlist?"

---

### Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

### Summary
`beforeAddLiquidity` ignores the `sender` argument (the actual `msg.sender` of `addLiquidity`) and gates on `owner` (the LP position recipient). Any address not on the allowlist can bypass the restriction by calling `addLiquidity(owner=allowlisted_address, ...)`.

### Finding Description
`DepositAllowlistExtension` is documented as gating `addLiquidity` **by depositor address**. [4](#0-3) 

The pool passes both the real caller (`sender = msg.sender`) and the position recipient (`owner`) to the extension hook: [1](#0-0) 

But the hook silently drops `sender` and evaluates `allowedDepositor[pool][owner]`: [5](#0-4) 

Because `owner` is a free caller-supplied parameter with no pool-level constraint (the pool imposes no restriction on who `owner` can be set to), any attacker can set `owner` to any allowlisted address and pass the check.

The `MetricOmmPoolLiquidityAdder` peripheral router explicitly supports depositing on behalf of another `owner`: [6](#0-5) 

Its only validation is `_validateOwner` (non-zero address check), not an allowlist check. The attacker can call the pool directly or through the router.

### Impact Explanation
The `DepositAllowlistExtension` allowlist is completely bypassed. Any address — regardless of allowlist status — can add liquidity to a restricted pool by naming any allowlisted address as `owner`. The pool admin's access control (e.g., KYC/compliance gating) is rendered entirely ineffective. The unauthorized caller's tokens are deposited and credited to the allowlisted address's LP position without that address's consent, which also constitutes unsolicited griefing of the position owner's accounting.

### Likelihood Explanation
The attack requires only a public `addLiquidity` call with a known allowlisted address as `owner`. No privileged access, special tokens, or oracle manipulation is needed. The allowlisted addresses are discoverable on-chain via `AllowedToDepositSet` events. Likelihood is high.

### Recommendation
Replace the `owner` check with the `sender` argument (the actual caller):

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

### Proof of Concept

```solidity
// attacker is NOT on the allowlist; allowlisted is on the allowlist
address attacker = makeAddr("attacker");
address allowlisted = makeAddr("allowlisted");

// pool admin allowlists only `allowlisted`
vm.prank(admin);
depositExtension.setAllowedToDeposit(address(pool), allowlisted, true);

// attacker calls addLiquidity with owner = allowlisted
// extension checks allowedDepositor[pool][allowlisted] == true → passes
vm.prank(attacker);
pool.addLiquidity(
    allowlisted,   // owner = allowlisted address
    0,
    deltas,
    callbackData,
    ""
);

// attacker successfully deposited despite not being on the allowlist
uint256 shares = pool.positionBinShares(allowlisted, 0, binIdx);
assertGt(shares, 0); // passes — allowlist bypassed
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-11)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
