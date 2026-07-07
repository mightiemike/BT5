### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables `usdcE` Drain Without Payment — (File: `core/contracts/ContractOwner.sol`)

---

### Summary
`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the return value. If the USDC token's `transferFrom` returns `false` instead of reverting, execution continues unconditionally, and the caller receives `usdcE` tokens from the `directDepositV1` address without having actually transferred any `usdc`.

---

### Finding Description
`replaceUsdcEWithUsdc` is an externally callable function with no access-control modifier. Its only gate is `require(block.chainid == 57073, ERR_UNAUTHORIZED)`. The function is designed to swap `usdcE` held in a `DirectDepositV1` address for `usdc` provided by the caller:

```solidity
// core/contracts/ContractOwner.sol, lines 608–620
function replaceUsdcEWithUsdc(bytes32 subaccount) external {
    require(block.chainid == 57073, ERR_UNAUTHORIZED);
    address payable directDepositV1 = directDepositV1Address[subaccount];
    require(directDepositV1 != address(0), "no dda");
    address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
    address usdc  = 0x2D270e6886d130D724215A266106e6832161EAEd;
    uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
    if (balance > 0) {
        IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance); // ← return value ignored
        DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));       // pulls usdcE to ContractOwner
        IERC20Base(usdcE).safeTransfer(msg.sender, balance);                 // sends usdcE to caller
    }
}
``` [1](#0-0) 

The raw `transferFrom` call on line 616 does not check the boolean return value. The rest of the codebase consistently uses `ERC20Helper.safeTransferFrom`, which wraps the call and reverts on a `false` return:

```solidity
// core/contracts/libraries/ERC20Helper.sol, lines 23–42
function safeTransferFrom(...) internal {
    (bool success, bytes memory data) = address(self).call(...);
    require(
        success && (data.length == 0 || abi.decode(data, (bool))),
        ERR_TRANSFER_FAILED
    );
}
``` [2](#0-1) 

`ContractOwner` already imports and uses `ERC20Helper` via `using ERC20Helper for IERC20Base` [3](#0-2) , making the omission here a clear inconsistency.

---

### Impact Explanation
If `usdc.transferFrom` returns `false` (e.g., the caller has zero allowance or zero balance and the token does not revert), the three-step sequence still executes:

1. `DirectDepositV1(directDepositV1).withdraw(usdcE)` — pulls the entire `usdcE` balance from the DDA into `ContractOwner`. [4](#0-3) 
2. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — sends that `usdcE` to the caller. [5](#0-4) 

The caller receives real `usdcE` tokens without providing any `usdc`. Every `directDepositV1` address on chain 57073 that holds a non-zero `usdcE` balance is drainable by any unprivileged address.

**Corrupted asset delta**: `usdcE` balance of `directDepositV1[subaccount]` → 0, with no corresponding `usdc` credit to the DDA. The subaccount's collateral backing is destroyed.

---

### Likelihood Explanation
- The function is callable by any EOA or contract on Ink chain (chainid 57073) — no role, signature, or privileged key required.
- The attacker only needs to identify a `subaccount` whose `directDepositV1Address` holds `usdcE` (observable on-chain).
- The attacker does not need to hold any `usdc` or set any allowance; they simply call the function with zero allowance and rely on the silent-failure behavior.
- The USDC token at the hardcoded address is a proxy contract; its `transferFrom` behavior is subject to the implementation behind the proxy. Non-reverting failure paths exist in several USDC variants and bridged stablecoin implementations.

---

### Recommendation
Replace the raw `transferFrom` call with `ERC20Helper.safeTransferFrom`, consistent with every other token transfer in the codebase:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [6](#0-5) 

This ensures the transaction reverts if the USDC transfer does not succeed, preventing the subsequent `withdraw` and `safeTransfer` from executing.

---

### Proof of Concept

1. A `directDepositV1` address for some `subaccount` holds `N` `usdcE` tokens (e.g., deposited by a legitimate user).
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with **zero `usdc` allowance**.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)` returns `false` (silent failure — no revert).
4. Return value is not checked; execution continues.
5. `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers `N` `usdcE` to `ContractOwner`.
6. `IERC20Base(usdcE).safeTransfer(attacker, N)` sends `N` `usdcE` to the attacker.
7. Attacker has received `N` `usdcE` at zero cost. The subaccount's DDA is drained.

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
