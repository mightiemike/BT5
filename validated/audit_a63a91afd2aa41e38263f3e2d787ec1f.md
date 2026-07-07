### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables usdcE Drain Without Providing USDC — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` directly without checking its return value, while the same function uses the safe wrapper (`safeTransfer`) for the outbound usdcE leg. Because the function has no caller access control, any unprivileged address can invoke it. If the USDC token on chain 57073 returns `false` on a failed transfer instead of reverting, the inbound USDC leg silently fails while the usdcE leg still executes, draining usdcE from the `directDepositV1` contract to the caller at zero cost.

---

### Finding Description

`ContractOwner.sol` declares `using ERC20Helper for IERC20Base` at line 24, making `safeTransfer` and `safeTransferFrom` available on every `IERC20Base` instance. The `ERC20Helper` library wraps both calls with a low-level `call` and explicitly checks both the call success flag and the decoded boolean return value. [1](#0-0) 

Inside `replaceUsdcEWithUsdc`, the outbound usdcE transfer correctly uses the safe wrapper: [2](#0-1) 

But the inbound USDC pull uses the raw interface call, discarding the return value: [3](#0-2) 

The function has no caller access control — only a chain ID guard — so any address can invoke it: [4](#0-3) 

`DirectDepositV1.withdraw` is `onlyOwner`, so only `ContractOwner` can call it, but `ContractOwner` does so unconditionally after the unchecked `transferFrom`: [5](#0-4) 

---

### Impact Explanation

If USDC on chain 57073 (Ink) returns `false` on a failed transfer rather than reverting (a known pattern for non-standard ERC20 tokens such as USDT-style implementations), the execution sequence becomes:

1. `transferFrom` returns `false` — no USDC moves from the caller to `directDepositV1`.
2. `DirectDepositV1.withdraw(usdcE)` executes unconditionally — usdcE moves from `directDepositV1` to `ContractOwner`.
3. `safeTransfer(msg.sender, balance)` executes — usdcE moves from `ContractOwner` to the attacker.

Net result: the attacker receives the full usdcE balance held by `directDepositV1` without providing any USDC. This is a direct, concrete asset loss for the subaccount whose `directDepositV1` is targeted.

---

### Likelihood Explanation

The function is callable by any unprivileged address on chain 57073 with no further preconditions. The only requirement is that the USDC token on that chain exhibits non-reverting failure behavior. Given that the protocol explicitly handles a USDC-to-USDCe migration (a real operational scenario), the function will be called in production. The inconsistency — `safeTransfer` used on line 618 but not on line 616 — indicates an oversight rather than a deliberate design choice.

---

### Recommendation

Replace the raw `transferFrom` call with the already-available `safeTransferFrom` wrapper from `ERC20Helper`:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

This is consistent with the `safeTransfer` call on line 618 and with the pattern used throughout the rest of the codebase (e.g., `EndpointStorage`, `BaseWithdrawPool`, `Airdrop`). [6](#0-5) 

---

### Proof of Concept

1. A `directDepositV1` for some `subaccount` holds `N` usdcE tokens.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` on chain 57073 without approving any USDC.
3. `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)` returns `false` (no revert) — no USDC transferred.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` transfers `N` usdcE to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(attacker, N)` transfers `N` usdcE to the attacker.
6. Attacker has gained `N` usdcE; `directDepositV1` has lost its entire usdcE balance; no USDC was deposited. [7](#0-6)

### Citations

**File:** core/contracts/libraries/ERC20Helper.sol (L23-41)
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
```

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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
