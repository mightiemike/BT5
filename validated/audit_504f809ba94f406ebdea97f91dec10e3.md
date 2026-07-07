### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables usdcE Theft from Direct Deposit Accounts — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` without checking the return value. If the USDC `transferFrom` returns `false` rather than reverting, the function continues to withdraw usdcE from the DDA and send it to the caller — delivering usdcE to the attacker at zero cost.

---

### Finding Description

`replaceUsdcEWithUsdc` is an externally callable function (no owner/admin guard, only a chain ID check) that is intended to swap usdcE held in a Direct Deposit Account for USDC. The swap logic is:

1. Pull USDC from `msg.sender` into the DDA via `transferFrom`.
2. Withdraw usdcE from the DDA to `ContractOwner`.
3. Forward usdcE to `msg.sender` via `safeTransfer`.

The critical flaw is at step 1:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

The return value of `transferFrom` is never checked. [1](#0-0) 

Every other outbound transfer in the codebase uses `ERC20Helper.safeTransfer` / `safeTransferFrom`, which performs a low-level `.call` and explicitly requires `success && (data.length == 0 || abi.decode(data, (bool)))`. [2](#0-1) 

The `IERC20Base` interface declares `transferFrom` as returning `bool`. [3](#0-2) 

If the USDC token on chain 57073 (Ink) returns `false` on a failed transfer (e.g., insufficient allowance) rather than reverting, Solidity 0.8.x will silently discard the `false` return value and execution continues. Steps 2 and 3 then execute unconditionally:

```solidity
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE)); // pulls usdcE to ContractOwner
IERC20Base(usdcE).safeTransfer(msg.sender, balance);           // sends usdcE to attacker
``` [4](#0-3) 

The `DirectDepositV1.withdraw` function unconditionally transfers the full usdcE balance to its caller (`ContractOwner`), which then forwards it to `msg.sender`. [5](#0-4) 

---

### Impact Explanation

**Impact: Medium.**

An unprivileged caller on chain 57073 can drain usdcE from any DDA that holds a usdcE balance, receiving the tokens without providing any USDC in return. The corrupted asset delta is the full usdcE balance of the targeted DDA. The impact is bounded to chain 57073 and to DDAs that still hold usdcE (the function exists specifically to migrate such balances).

---

### Likelihood Explanation

**Likelihood: Medium.**

The function is callable by any address on chain 57073 with no privilege requirement beyond the chain ID check. [6](#0-5) 

Exploitability depends on whether the USDC deployment on Ink returns `false` on failure or reverts. Many USDC variants (e.g., Circle's FiatToken) revert on failure, which would prevent exploitation. However, the code makes no assumption about this behavior and provides no safety net. If the deployed USDC returns `false`, the exploit is immediately reachable by any caller who identifies a DDA with a non-zero usdcE balance.

---

### Recommendation

Replace the bare `transferFrom` call with `ERC20Helper.safeTransferFrom`, consistent with every other transfer site in the codebase:

```solidity
// Before (unsafe):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe):
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`safeTransferFrom` uses a low-level `.call` and requires `success && (data.length == 0 || abi.decode(data, (bool)))`, which correctly handles both reverting and `false`-returning tokens. [7](#0-6) 

---

### Proof of Concept

1. Identify a subaccount whose DDA (`directDepositV1Address[subaccount]`) holds a non-zero usdcE balance on chain 57073.
2. Call `ContractOwner.replaceUsdcEWithUsdc(subaccount)` from an EOA with **zero USDC allowance**.
3. If the USDC `transferFrom` returns `false` (no revert), execution continues past line 616.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers the full usdcE balance from the DDA to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` forwards the usdcE to the attacker.
6. The attacker receives usdcE; no USDC was ever transferred.

### Citations

**File:** core/contracts/ContractOwner.sol (L608-611)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
```

**File:** core/contracts/ContractOwner.sol (L615-619)
```text
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L9-21)
```text
    function safeTransfer(
        IERC20Base self,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(IERC20Base.transfer.selector, to, amount)
        );
        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
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

**File:** core/contracts/interfaces/IERC20Base.sol (L22-30)
```text
     *
     * Emits a {Transfer} event.
     */
    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) external returns (bool);

```

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
