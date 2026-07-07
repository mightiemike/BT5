### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables USDC.e Theft Without Providing USDC — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` at line 616 without checking its return value and without using the protocol's own `ERC20Helper.safeTransferFrom` wrapper. If the call returns `false` instead of reverting, execution continues: USDC.e is withdrawn from the `directDepositV1` address and sent to the caller — without the caller ever providing USDC.

---

### Finding Description

`replaceUsdcEWithUsdc` is a public, permissionless function (restricted only to chain ID 57073) that is intended to swap USDC.e held in a `DirectDepositV1` address for USDC provided by the caller: [1](#0-0) 

The three-step sequence at lines 616–618 is:
1. Pull `balance` USDC from `msg.sender` into `directDepositV1` via `transferFrom`.
2. Withdraw all USDC.e from `directDepositV1` to `ContractOwner` via `DirectDepositV1.withdraw`.
3. Send that USDC.e to `msg.sender` via `safeTransfer`.

Step 1 uses a raw, unchecked `transferFrom`: [2](#0-1) 

The return value is silently discarded. Steps 2 and 3 execute unconditionally regardless of whether step 1 succeeded.

The rest of the protocol consistently uses `ERC20Helper.safeTransferFrom`, which low-level calls `transferFrom` and requires both `success == true` and a truthy decoded return value: [3](#0-2) 

This one call site is the sole deviation from that pattern across the entire codebase.

---

### Impact Explanation

If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on chain 57073 returns `false` on a failed `transferFrom` (e.g., insufficient allowance, non-standard implementation, or a future token upgrade), the attacker:

- Provides **zero USDC**
- Receives the full USDC.e balance held in any `directDepositV1` address

This is a direct, concrete asset theft. The corrupted asset delta is: `directDepositV1.usdcE_balance → attacker`, with no corresponding `usdc` credit to `directDepositV1`. Every `directDepositV1` address on chain 57073 that holds a non-zero USDC.e balance is at risk.

---

### Likelihood Explanation

The function has no `onlyOwner` or `onlyDeployer` modifier — any unprivileged caller on chain 57073 can invoke it. The only precondition is that a `directDepositV1` address exists for the target `subaccount` and holds a non-zero USDC.e balance, both of which are observable on-chain. The exploitability depends on whether the specific USDC deployment returns `false` vs. reverts; non-reverting behavior is common in non-Circle USDC forks and bridged variants.

---

### Recommendation

Replace the bare `transferFrom` call with the protocol's existing `safeTransferFrom` helper:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper.safeTransferFrom` is already imported and in scope via `using ERC20Helper for IERC20Base` at line 24 of `ContractOwner.sol`. [4](#0-3) 

---

### Proof of Concept

1. Identify a `subaccount` whose `directDepositV1Address[subaccount]` is non-zero and holds USDC.e balance (observable on-chain).
2. Call `replaceUsdcEWithUsdc(subaccount)` from an EOA with **zero USDC allowance** granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` returns `false` (no allowance) — return value ignored, no revert.
4. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` executes: USDC.e is transferred from `directDepositV1` to `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` executes: attacker receives full USDC.e balance.
6. Net result: attacker gains `balance` USDC.e; `directDepositV1` loses its USDC.e; no USDC was ever deposited.

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
