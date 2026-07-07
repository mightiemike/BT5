### Title
Unchecked Return Value on Raw `transferFrom` in `replaceUsdcEWithUsdc` Enables USDC.e Theft — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` uses a raw `IERC20Base.transferFrom` call whose boolean return value is never checked. If the USDC token at the hardcoded address returns `false` on failure (insufficient allowance or balance) rather than reverting, execution continues and the caller receives USDC.e from the target `DirectDepositV1` contract without having provided any USDC in exchange.

---

### Finding Description

`ContractOwner` declares `using ERC20Helper for IERC20Base` at the top of the file, making `safeTransferFrom` available on every `IERC20Base` instance. Every other token transfer in the codebase — in `EndpointStorage`, `Clearinghouse`, `BaseWithdrawPool` — uses `safeTransfer` / `safeTransferFrom` from `ERC20Helper`, which low-level-calls the token and requires `success && (data.length == 0 || abi.decode(data, (bool)))`.

The single exception is line 616 of `replaceUsdcEWithUsdc`:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

This calls the ABI-decoded `transferFrom` directly. If the token returns `false` (BAT-style), the Solidity ABI decoder silently discards it and execution falls through. The two subsequent lines then unconditionally drain USDC.e out of `directDepositV1` and send it to `msg.sender`:

```solidity
DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE)); // USDC.e → ContractOwner
IERC20Base(usdcE).safeTransfer(msg.sender, balance);           // USDC.e → attacker
```

The function carries no `onlyOwner` modifier; the only gate is `block.chainid == 57073`. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

An attacker on chain 57073 (Ink) who has zero USDC allowance to `ContractOwner` can call `replaceUsdcEWithUsdc(subaccount)` for any `subaccount` whose `DirectDepositV1` holds a non-zero USDC.e balance. If the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` on a failed transfer rather than reverting, the attacker receives the full USDC.e balance of that `DirectDepositV1` for free. This is a direct, unrecoverable asset loss for the subaccount owner. [4](#0-3) 

---

### Likelihood Explanation

The function is `external` with no access control beyond a chain-ID check, making it reachable by any unprivileged caller on chain 57073. The exploitability depends on whether the specific USDC deployment at `0x2D270e6886d130D724215A266106e6832161EAEd` returns `false` on failure rather than reverting. Even if the current deployment reverts, the pattern is fragile: a token upgrade or a future product addition using a BAT-style token would immediately open the drain path. The rest of the codebase consistently uses `safeTransferFrom` precisely to guard against this class of token. [5](#0-4) 

---

### Recommendation

Replace the raw call with the `safeTransferFrom` wrapper already imported and in scope:

```solidity
// Before (unsafe)
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After (safe)
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper.safeTransferFrom` low-level-calls the token and requires `success && (data.length == 0 || abi.decode(data, (bool)))`, handling both the no-return-value case (USDT-style) and the false-return case (BAT-style). [3](#0-2) 

---

### Proof of Concept

1. A `DirectDepositV1` for `subaccount` exists on chain 57073 and holds `N` USDC.e.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` with zero USDC balance/allowance.
3. `balance = IERC20Base(usdcE).balanceOf(directDepositV1)` → `N > 0`, enters the `if` branch.
4. `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, N)` → token returns `false`; return value is not checked; execution continues.
5. `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` → transfers `N` USDC.e from `directDepositV1` to `ContractOwner`.
6. `IERC20Base(usdcE).safeTransfer(msg.sender, N)` → transfers `N` USDC.e from `ContractOwner` to attacker.
7. Attacker has received `N` USDC.e; the subaccount's `DirectDepositV1` is drained; no USDC was ever transferred in. [6](#0-5) [7](#0-6)

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

**File:** core/contracts/EndpointStorage.sol (L83-93)
```text
    function chargeSlowModeFee(IERC20Base token, address from)
        internal
        virtual
    {
        require(address(token) != address(0));
        token.safeTransferFrom(
            from,
            address(this),
            clearinghouse.getSlowModeFee()
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
